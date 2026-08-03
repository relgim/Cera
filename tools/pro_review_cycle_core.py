from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from cera.continuous.job4_terminal import (
    ContinuousJob4TerminalEvidenceV6,
    decode_continuous_job4_terminal_evidence,
)
from cera.continuous.job4_diagnostics import ContinuousJob4TestDiagnosticsV1


SPEC_SCHEMA = "cera.pro_review_cycle_spec.v2"
SPEC_SCHEMA_V3 = "cera.pro_review_cycle_spec.v3"
MANIFEST_SCHEMA = "cera.pro_review_cycle_manifest.v2"
MANIFEST_SCHEMA_V3 = "cera.pro_review_cycle_manifest.v3"
LEGACY_MANIFEST_SCHEMA = "cera.pro_review_cycle_manifest.v1"
FAILED_PRE_MANIFEST_TOMBSTONE_SCHEMA = (
    "cera.pro_review_failed_pre_manifest_tombstone.v1"
)
FAILED_PUBLICATION_RECEIPT_SCHEMA = "cera.pro_review_cycle_publication_failure.v1"
STATE_SCHEMA = "cera.pro_review_cycle_state.v2"
RECEIPT_SCHEMA = "cera.pro_review_cycle_receipt.v2"
JOB4_AUTHORIZATION_SCHEMA = "cera.pro_review_job4_authorization.v1"
JOB4_RESULT_SCHEMA = "cera.pro_review_job4_result.v1"
JOB4_RESULT_SCHEMA_V2 = "cera.pro_review_job4_result.v2"
JOB4_RESULT_SCHEMA_V3 = "cera.pro_review_job4_result.v3"

STATE_JOB4_IN_PROGRESS = "job4_in_progress"
STATE_RESPONSE_PENDING = "job4_complete_response_pending"
STATE_REVIEW_CONSUMED = "review_consumed_advisory"

EXPECTED_RESPONSE_RELATIVE_PATH = Path("inbox") / "PRO_RESPONSE.md"
ACCEPTED_RESPONSE_RELATIVE_PATH = Path("accepted") / "PRO_RESPONSE.md"
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_SNAPSHOT_FILES = 4096
MAX_SNAPSHOT_TOTAL_BYTES = 64 * 1024 * 1024
DEFAULT_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
RESPONSE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
FORBIDDEN_ROOTS = {".git", ".tmp", ".venv", "runtime"}
FORBIDDEN_PARTS = {
    "credentials",
    "credential",
    "secrets",
    "secret",
}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".key", ".pem"}

FAILED_PRE_MANIFEST_ABSENT_ARTIFACTS = (
    "CYCLE_MANIFEST.json",
    "receipts/PUBLISHED.json",
    "receipts/TRIGGER_SENT.json",
    "receipts/JOB4_STARTED.json",
    "receipts/JOB4_COMPLETED.json",
    "source/JOB4_RESULT.json",
    "artifacts/JOB4_RESULT.json",
    "accepted/PRO_RESPONSE.md",
    "receipts/RESPONSE_CONSUMED.json",
)

REVIEW_CONTEXT_FIELDS = (
    "creator_goal",
    "starting_baseline_or_prior_checkpoint",
    "selection_rationale",
    "diff_summary",
    "focused_tests",
    "complete_suite",
    "active_profile_before",
    "active_profile_after",
    "provider_calls_and_cost",
    "retry_and_fallback",
    "story_database_and_branch_effects",
    "user_visible_effect",
    "historical_evidence_integrity",
    "unresolved_defects",
    "uncertainty_and_risks",
    "disagreement_with_prior_review",
    "codex_advisory_next_candidates",
    "questions_for_chatgpt_pro",
    "explicit_exclusions",
)

RECEIPT_COMMON_FIELDS = (
    "schema_version",
    "cycle_id",
    "event",
    "manifest_root_sha256",
    "predecessor_receipt_sha256",
    "recorded_at_utc",
)
RECEIPT_EVENT_FIELDS = {
    "jobs_1_3_published": (
        "task_set_sha256",
        "source_root_sha256",
        "outbox_sha256",
        "package_publication",
    ),
    "job4_started": (
        "job4_task_id",
        "job4_scope_sha256",
        "authorization_record_sha256",
        "authorization_record_contract_validated",
        "unrelated_work_authorized",
    ),
    "review_trigger_sent": (
        "transport",
        "target_id_sha256",
        "message_sha256",
        "app_result_sha256",
        "app_result_attested_success",
        "independent_delivery_proof",
        "raw_target_message_or_app_result_retained",
    ),
    "job4_completed": (
        "job4_task_id",
        "job4_status",
        "job4_result_sha256",
        "job4_result_relative_path",
        "job4_report_sha256",
        "job4_report_relative_path",
        "effect_claim_source",
        "effects",
    ),
    "job4_completed_v2": (
        "job4_task_id",
        "job4_status",
        "job4_result_sha256",
        "job4_result_relative_path",
        "job4_report_sha256",
        "job4_report_relative_path",
        "terminal_evidence_sha256",
        "terminal_evidence_relative_path",
        "effect_claim_source",
        "effects",
    ),
    "job4_completed_v3": (
        "job4_task_id",
        "job4_status",
        "job4_result_sha256",
        "job4_result_relative_path",
        "job4_report_sha256",
        "job4_report_relative_path",
        "terminal_evidence_sha256",
        "terminal_evidence_relative_path",
        "test_diagnostics_sha256",
        "test_records_root_sha256",
        "test_record_count",
        "effect_claim_source",
        "effects",
    ),
    "response_consumed": (
        "response_sha256",
        "response_relative_path",
        "review_disposition",
        "identity_validation",
        "completeness_validation",
        "stable_read_validation",
        "advisory_only",
        "creator_authority_granted",
    ),
}


class CycleError(RuntimeError):
    pass


class ResponseNotReady(CycleError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise CycleError(f"required file is missing: {path}") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CycleError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise CycleError(f"JSON root must be an object: {path}")
    return value


def atomic_replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def immutable_write(path: Path, data: bytes) -> bool:
    """Publish complete bytes without ever replacing a concurrent winner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if not path.is_file() or path.read_bytes() != data:
                raise CycleError(
                    f"immutable file conflicts with existing content: {path}"
                )
            return False
        return True
    finally:
        if temporary.exists():
            temporary.unlink()


def receipt_write(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    if path.exists():
        existing = read_json(path)
        strip_time = lambda item: {
            key: content
            for key, content in item.items()
            if key != "recorded_at_utc"
        }
        if strip_time(existing) != strip_time(value):
            raise CycleError(f"receipt conflicts with existing event: {path}")
        return existing
    immutable_write(path, canonical_json_bytes(value))
    return dict(value)


def exact_keys(value: Mapping[str, Any], required: Iterable[str], label: str) -> None:
    expected = set(required)
    actual = set(value)
    if actual != expected:
        raise CycleError(
            f"{label} fields do not match contract; "
            f"missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}"
        )


def require_string(
    value: Any, label: str, *, min_length: int = 1, max_length: int = 20000
) -> str:
    if not isinstance(value, str):
        raise CycleError(f"{label} must be a string")
    candidate = value.strip()
    if len(candidate) < min_length or len(candidate) > max_length:
        raise CycleError(f"{label} length is outside the contract")
    return candidate


def require_id(value: Any, label: str) -> str:
    candidate = require_string(value, label, max_length=128)
    if not SAFE_ID.fullmatch(candidate):
        raise CycleError(f"{label} is not a safe identifier")
    return candidate


def require_hash(value: Any, label: str, *, git_object: bool = False) -> str:
    candidate = require_string(value, label, max_length=64).lower()
    pattern = GIT_OBJECT_ID if git_object else SHA256
    if not pattern.fullmatch(candidate):
        raise CycleError(f"{label} is not a valid lowercase hash")
    return candidate


def repository_root(value: Path | None = None) -> Path:
    root = (value or DEFAULT_REPOSITORY_ROOT).resolve(strict=True)
    if not root.is_dir():
        raise CycleError("repository root is not a directory")
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise CycleError("repository root is not a Git worktree")
    observed = Path(result.stdout.strip()).resolve(strict=True)
    if observed != root:
        raise CycleError("configured repository root does not match Git")
    return root


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise CycleError("path escapes the CERA repository") from exc


def has_link_component(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    current = root
    junction_check = getattr(os.path, "isjunction", lambda _: False)
    for part in relative.parts:
        current = current / part
        if current.exists() and (current.is_symlink() or junction_check(current)):
            return True
    return False


def resolve_inside(
    root: Path,
    path_value: Any,
    label: str,
    *,
    must_exist: bool,
    suffixes: set[str] | None = None,
) -> Path:
    path = Path(require_string(path_value, label)).expanduser()
    if not path.is_absolute():
        raise CycleError(f"{label} must be absolute")
    try:
        resolved = path.resolve(strict=must_exist)
    except FileNotFoundError as exc:
        raise CycleError(f"{label} is missing") from exc
    rel = relative_path(root, resolved)
    ordered_parts = tuple(part.casefold() for part in Path(rel).parts)
    parts = set(ordered_parts)
    if (ordered_parts and ordered_parts[0] in FORBIDDEN_ROOTS) or parts.intersection(
        FORBIDDEN_PARTS
    ):
        raise CycleError(f"{label} crosses a protected repository directory")
    if resolved.suffix.casefold() in FORBIDDEN_SUFFIXES:
        raise CycleError(f"{label} names a protected file type")
    if "secret" in resolved.name.casefold() or "credential" in resolved.name.casefold():
        raise CycleError(f"{label} appears to name protected material")
    if has_link_component(root, resolved):
        raise CycleError(f"{label} contains a symlink or junction")
    if suffixes is not None and resolved.suffix.casefold() not in suffixes:
        raise CycleError(f"{label} has an unsupported file type")
    if must_exist:
        if not resolved.is_file():
            raise CycleError(f"{label} must name a regular file")
        if resolved.stat().st_size > MAX_ARTIFACT_BYTES:
            raise CycleError(f"{label} exceeds the artifact size limit")
    return resolved


def cycle_path(root: Path, value: Path, cycle_id: str | None = None) -> Path:
    path = value.resolve()
    expected_parent = root / ".chatgpt" / "pro-review" / "cycles"
    if path.parent != expected_parent:
        raise CycleError("cycle directory must be a direct child of the CERA cycle root")
    if cycle_id is not None and path.name != cycle_id:
        raise CycleError("cycle directory leaf must equal cycle_id")
    if has_link_component(root, path):
        raise CycleError("cycle directory cannot contain a symlink or junction")
    return path


def verify_git_object(root: Path, value: Any, label: str) -> str:
    object_id = require_hash(value, label, git_object=True)
    result = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{object_id}^{{object}}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise CycleError(f"{label} does not exist in the CERA repository")
    return object_id


def verified_file(
    root: Path,
    path_value: Any,
    hash_value: Any,
    label: str,
    *,
    suffixes: set[str] | None = None,
    utf8: bool = False,
) -> tuple[Path, bytes, str]:
    path = resolve_inside(
        root, path_value, f"{label}.path", must_exist=True, suffixes=suffixes
    )
    expected = require_hash(hash_value, f"{label}.sha256")
    data = path.read_bytes()
    actual = sha256_bytes(data)
    if actual != expected:
        raise CycleError(f"{label} hash mismatch")
    if utf8:
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CycleError(f"{label} is not UTF-8") from exc
    return path, data, actual


def repository_relative_file(
    root: Path,
    path_value: Any,
    label: str,
    *,
    suffixes: set[str] | None = None,
) -> Path:
    value = require_string(path_value, label)
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise CycleError(f"{label} must be a safe repository-relative path")
    path = resolve_inside(
        root,
        str(root / candidate),
        label,
        must_exist=True,
        suffixes=suffixes,
    )
    if relative_path(root, path) != candidate.as_posix():
        raise CycleError(f"{label} is not canonical")
    return path


def stable_read(path: Path, delay_milliseconds: int) -> tuple[bytes, str]:
    if delay_milliseconds < 0:
        raise CycleError("stability delay must be non-negative")
    try:
        first_stat = path.stat()
        first = path.read_bytes()
    except FileNotFoundError as exc:
        raise ResponseNotReady(f"expected file is not ready: {path}") from exc
    if not path.is_file() or len(first) > MAX_ARTIFACT_BYTES:
        raise CycleError("stable-read target is invalid")
    if delay_milliseconds:
        time.sleep(delay_milliseconds / 1000)
    try:
        second_stat = path.stat()
        second = path.read_bytes()
    except FileNotFoundError as exc:
        raise CycleError("file changed during the stable-read interval") from exc
    if (
        first_stat.st_size != second_stat.st_size
        or first_stat.st_mtime_ns != second_stat.st_mtime_ns
        or first != second
    ):
        raise CycleError("file changed during the stable-read interval")
    if not second:
        raise CycleError("stable-read target is empty")
    return second, sha256_bytes(second)


def decode_git_path(value: bytes, label: str) -> str:
    try:
        path = value.decode("utf-8").replace("\\", "/")
    except UnicodeDecodeError as exc:
        raise CycleError(f"{label} is not UTF-8") from exc
    parts = Path(path).parts
    if not path or Path(path).is_absolute() or ".." in parts:
        raise CycleError(f"{label} is not a safe repository-relative path")
    return path


def git_status_changes(root: Path) -> list[dict[str, str | None]]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise CycleError("unable to inspect repository status")
    tokens = result.stdout.split(b"\0")
    changes: list[dict[str, str | None]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        try:
            text = token.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CycleError("Git status contains a non-UTF-8 path") from exc
        if len(text) < 4:
            raise CycleError("Git status returned malformed porcelain output")
        status = text[:2]
        path = decode_git_path(text[3:].encode("utf-8"), "Git status path")
        original_path = None
        if "R" in status or "C" in status:
            if index >= len(tokens) or not tokens[index]:
                raise CycleError("Git rename status is incomplete")
            original_path = decode_git_path(tokens[index], "Git original path")
            index += 1
        changes.append(
            {"status": status, "path": path, "original_path": original_path}
        )
    return sorted(
        changes,
        key=lambda item: (
            str(item["path"]),
            str(item["original_path"] or ""),
            str(item["status"]),
        ),
    )


def snapshot_exclusions(cycle_id: str) -> tuple[dict[str, str], ...]:
    return (
        {
            "path_prefix": "runtime/",
            "reason_code": "generated_runtime_state",
        },
        {
            "path_prefix": ".chatgpt/operations/",
            "reason_code": "connector_metadata_outside_task",
        },
        {
            "path_prefix": f".chatgpt/pro-review/cycles/{cycle_id}/",
            "reason_code": "current_cycle_transport_state",
        },
    )


def build_snapshot(
    root: Path, cycle_id: str, value: Any, baseline_git_sha: str
) -> tuple[dict[str, Any], bytes, bytes]:
    if not isinstance(value, dict):
        raise CycleError("review_snapshot must be an object")
    exact_keys(
        value,
        ("baseline_git_sha", "include_all_nonexcluded_changes", "declared_exclusions"),
        "review_snapshot",
    )
    if verify_git_object(root, value["baseline_git_sha"], "snapshot baseline") != baseline_git_sha:
        raise CycleError("review snapshot baseline differs from checkpoint Git object")
    if value["include_all_nonexcluded_changes"] is not True:
        raise CycleError("review snapshot must include all nonexcluded changes")
    expected_exclusions = list(snapshot_exclusions(cycle_id))
    if value["declared_exclusions"] != expected_exclusions:
        raise CycleError("review snapshot exclusions do not match the fixed contract")

    changed = git_status_changes(root)
    included: list[dict[str, str | None]] = []
    excluded: list[dict[str, str | None]] = []
    for change in changed:
        paths = [str(change["path"])]
        if change["original_path"] is not None:
            paths.append(str(change["original_path"]))
        matches = [
            next(
                (
                    item
                    for item in expected_exclusions
                    if path.startswith(item["path_prefix"])
                ),
                None,
            )
            for path in paths
        ]
        if any(item is not None for item in matches):
            if not all(item == matches[0] for item in matches):
                raise CycleError("Git change crosses a snapshot exclusion boundary")
            excluded.append({**change, "reason_code": matches[0]["reason_code"]})
        else:
            included.append(change)

    entries: list[dict[str, Any]] = []
    if len(included) > MAX_SNAPSHOT_FILES:
        raise CycleError("changed-source snapshot exceeds the file-count ceiling")
    archived_file_count = 0
    archived_total_bytes = 0
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(
        archive_buffer, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for change in included:
            relative = str(change["path"])
            if change["original_path"] is not None:
                resolve_inside(
                    root,
                    str(root / str(change["original_path"])),
                    f"snapshot original path {change['original_path']}",
                    must_exist=False,
                )
            candidate = root / relative
            present = candidate.exists()
            if not present and "D" not in str(change["status"]):
                raise CycleError(f"changed path is missing without deletion status: {relative}")
            if present:
                source = resolve_inside(
                    root, str(candidate), f"snapshot {relative}", must_exist=True
                )
                data = source.read_bytes()
                archived_file_count += 1
                archived_total_bytes += len(data)
                if archived_total_bytes > MAX_SNAPSHOT_TOTAL_BYTES:
                    raise CycleError("changed-source snapshot exceeds the byte ceiling")
                digest: str | None = sha256_bytes(data)
                size: int | None = len(data)
                content_state = "present"
            else:
                resolve_inside(
                    root, str(candidate), f"snapshot deletion {relative}", must_exist=False
                )
                data = b""
                digest = None
                size = None
                content_state = "deleted"
            entry = {
                "status": change["status"],
                "path": relative,
                "original_path": change["original_path"],
                "content_state": content_state,
                "sha256": digest,
                "size": size,
            }
            entries.append(entry)
            if present:
                info = zipfile.ZipInfo(f"files/{relative}")
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)

    manifest_without_root: dict[str, Any] = {
        "schema_version": "cera.pro_review_changed_source_manifest.v2",
        "repository_identity_sha256": sha256_text(str(root).casefold()),
        "baseline_git_sha": baseline_git_sha,
        "included_changes": entries,
        "excluded_status_changes": excluded,
        "archived_file_count": archived_file_count,
        "archived_total_bytes": archived_total_bytes,
        "file_count_ceiling": MAX_SNAPSHOT_FILES,
        "total_byte_ceiling": MAX_SNAPSHOT_TOTAL_BYTES,
    }
    source_root = sha256_bytes(canonical_json_bytes(manifest_without_root))
    manifest = {**manifest_without_root, "source_root_sha256": source_root}
    manifest_bytes = canonical_json_bytes(manifest)
    archive_bytes = archive_buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
        if archive.testzip() is not None:
            raise CycleError("generated source snapshot ZIP failed integrity")
    return manifest, manifest_bytes, archive_bytes


def validate_zip(data: bytes, label: str) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
            if not archive.namelist() or archive.testzip() is not None:
                raise CycleError(f"{label} ZIP is empty or corrupt")
            if sum(info.file_size for info in archive.infolist()) > MAX_ARTIFACT_BYTES:
                raise CycleError(f"{label} ZIP expands beyond the size limit")
    except zipfile.BadZipFile as exc:
        raise CycleError(f"{label} is not a valid ZIP") from exc


def validate_review_context(value: Any, job_count: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CycleError("review_context must be an object")
    exact_keys(value, REVIEW_CONTEXT_FIELDS, "review_context")
    result: dict[str, Any] = {}
    for field in REVIEW_CONTEXT_FIELDS:
        content = value[field]
        if field in {
            "selection_rationale",
            "codex_advisory_next_candidates",
            "questions_for_chatgpt_pro",
            "explicit_exclusions",
        }:
            if not isinstance(content, list) or not all(
                isinstance(item, str) and item.strip() for item in content
            ):
                raise CycleError(f"review_context.{field} must be a non-empty string list")
            if field == "selection_rationale" and len(content) != job_count:
                raise CycleError("selection_rationale must have one item per progression")
            result[field] = [item.strip() for item in content]
        else:
            result[field] = require_string(content, f"review_context.{field}")
    return result


def task_result(root: Path, value: Any, label: str) -> tuple[dict[str, str], bytes]:
    if not isinstance(value, dict):
        raise CycleError(f"{label} must be an object")
    exact_keys(value, ("task_id", "result_path", "result_sha256"), label)
    task_id = require_id(value["task_id"], f"{label}.task_id")
    path, data, digest = verified_file(
        root,
        value["result_path"],
        value["result_sha256"],
        label,
        suffixes={".md"},
        utf8=True,
    )
    text = data.decode("utf-8-sig")
    task_matches = re.findall(
        r"(?m)^task_id:\s*`?([A-Za-z0-9][A-Za-z0-9._-]{0,127})`?\s*$", text
    )
    status_matches = re.findall(
        r"(?m)^status:\s*`?(completed|blocked)`?\s*$",
        text,
    )
    if task_matches != [task_id] or len(status_matches) != 1:
        raise CycleError(
            f"{label} document must declare the exact task_id and one final status"
        )
    return {
        "task_id": task_id,
        "status": status_matches[0],
        "source_path": str(path),
        "sha256": digest,
    }, data


def validate_authorization(
    root: Path,
    cycle: Path,
    cycle_id: str,
    task_id: str,
    scope: str,
    path_value: Any,
    hash_value: Any,
) -> tuple[dict[str, Any], bytes, str]:
    path, data, digest = verified_file(
        root,
        path_value,
        hash_value,
        "job4.authorization_record",
        suffixes={".json"},
        utf8=True,
    )
    if path.parent != cycle / "source":
        raise CycleError("Job 4 authorization record must be in the cycle source directory")
    record = json.loads(data.decode("utf-8-sig"))
    if not isinstance(record, dict):
        raise CycleError("Job 4 authorization record must be an object")
    exact_keys(
        record,
        (
            "schema_version",
            "cycle_id",
            "task_id",
            "scope",
            "scope_sha256",
            "expected_result_relative_path",
            "authority_source_path",
            "authority_source_sha256",
            "explicit_exclusions",
        ),
        "job4 authorization",
    )
    if record["schema_version"] != JOB4_AUTHORIZATION_SCHEMA:
        raise CycleError("unsupported Job 4 authorization schema")
    expected_result = "source/JOB4_RESULT.json"
    if (
        record["cycle_id"] != cycle_id
        or record["task_id"] != task_id
        or record["scope"] != scope
        or record["scope_sha256"] != sha256_text(scope)
        or record["expected_result_relative_path"] != expected_result
    ):
        raise CycleError("Job 4 authorization does not bind the exact cycle/task/scope")
    exclusions = record["explicit_exclusions"]
    if not isinstance(exclusions, list) or not exclusions:
        raise CycleError("Job 4 authorization must name explicit exclusions")
    source_path, _, source_hash = verified_file(
        root,
        record["authority_source_path"],
        record["authority_source_sha256"],
        "job4.authority_source",
        utf8=True,
    )
    record["authority_source_path"] = str(source_path)
    record["authority_source_sha256"] = source_hash
    return record, data, digest


def load_manifest_file(path: Path) -> dict[str, Any]:
    manifest = read_json(path)
    schema = manifest.get("schema_version")
    if schema not in {
        MANIFEST_SCHEMA,
        MANIFEST_SCHEMA_V3,
        LEGACY_MANIFEST_SCHEMA,
    }:
        raise CycleError("unsupported cycle manifest schema")
    if schema in {MANIFEST_SCHEMA, MANIFEST_SCHEMA_V3}:
        expected = manifest.get("manifest_root_sha256")
        unsigned = {key: value for key, value in manifest.items() if key != "manifest_root_sha256"}
        if not isinstance(expected, str) or sha256_bytes(canonical_json_bytes(unsigned)) != expected:
            raise CycleError("cycle manifest root hash mismatch")
    return manifest


def _validate_failed_publication_receipt(
    receipt: Mapping[str, Any], sequence: int
) -> None:
    if receipt.get("schema_version") != FAILED_PUBLICATION_RECEIPT_SCHEMA:
        raise CycleError("unsupported failed-publication receipt schema")
    if (
        receipt.get("attempted_cycle_sequence") != sequence
        or receipt.get("disposition")
        != "publication_failed_pre_manifest_pre_job4"
        or receipt.get("identity_reusable") is not False
        or receipt.get("cycle_directory_created") is not True
        or receipt.get("manifest_published") is not False
        or receipt.get("published_receipt_created") is not False
        or receipt.get("job4_started_receipt_created") is not False
        or receipt.get("trigger_sent_receipt_created", False) is not False
    ):
        raise CycleError("failed-publication receipt does not prove a pre-manifest failure")
    require_id(receipt.get("attempted_cycle_id"), "failed receipt cycle_id")
    require_id(receipt.get("attempted_checkpoint_id"), "failed receipt checkpoint_id")
    require_string(receipt.get("failure_type"), "failed receipt failure_type")
    require_string(receipt.get("failure_message"), "failed receipt failure_message")
    provider_calls = receipt.get("provider_calls")
    if not isinstance(provider_calls, dict):
        raise CycleError("failed-publication provider calls must be an object")
    exact_keys(
        provider_calls,
        ("codex_family", "deepseek", "terra"),
        "failed-publication provider calls",
    )
    if any(value != 0 or isinstance(value, bool) for value in provider_calls.values()):
        raise CycleError("failed-publication receipt contains nonzero provider calls")
    for field in (
        "story_database_writes",
        "route_changes",
        "service_changes",
        "installed_sillytavern_changes",
    ):
        if receipt.get(field) != 0 or isinstance(receipt.get(field), bool):
            raise CycleError("failed-publication receipt contains nonzero product effects")


def _failed_tombstone_fields() -> tuple[str, ...]:
    return (
        "schema_version",
        "attempted_cycle_sequence",
        "attempted_cycle_id",
        "attempted_checkpoint_id",
        "attempted_run_id",
        "attempted_job4_task_id",
        "original_failure_receipt_path",
        "original_failure_receipt_sha256",
        "source_local_receipt_copy_path",
        "source_local_receipt_copy_sha256",
        "attempted_cycle_spec_path",
        "attempted_cycle_spec_sha256",
        "authorization_record_path",
        "authorization_record_sha256",
        "disposition",
        "failure_type",
        "failure_message",
        "identity_reusable",
        "provider_calls",
        "effects",
        "absent_artifacts",
        "residual_cycle_inventory",
    )


def _validate_optional_path_hash(
    path_value: Any, hash_value: Any, label: str
) -> tuple[str | None, str | None]:
    if path_value is None or hash_value is None:
        if path_value is not None or hash_value is not None:
            raise CycleError(f"{label} path/hash must both be null or both be present")
        return None, None
    path = require_string(path_value, f"{label}.path")
    digest = require_hash(hash_value, f"{label}.sha256")
    return path, digest


def _validate_tombstone_semantics(
    tombstone: Mapping[str, Any], receipt: Mapping[str, Any], sequence: int
) -> None:
    exact_keys(tombstone, _failed_tombstone_fields(), "failed-pre-manifest tombstone")
    if tombstone.get("schema_version") != FAILED_PRE_MANIFEST_TOMBSTONE_SCHEMA:
        raise CycleError("unsupported failed-pre-manifest tombstone schema")
    if tombstone.get("attempted_cycle_sequence") != sequence:
        raise CycleError("failed-pre-manifest tombstone sequence is invalid")
    if (
        tombstone.get("attempted_cycle_id") != receipt.get("attempted_cycle_id")
        or tombstone.get("attempted_checkpoint_id")
        != receipt.get("attempted_checkpoint_id")
        or tombstone.get("disposition") != receipt.get("disposition")
        or tombstone.get("failure_type") != receipt.get("failure_type")
        or tombstone.get("failure_message") != receipt.get("failure_message")
        or tombstone.get("identity_reusable") is not False
    ):
        raise CycleError("failed-pre-manifest tombstone identity does not match its receipt")
    expected_run = receipt.get("attempted_run_id")
    if tombstone.get("attempted_run_id") != expected_run:
        raise CycleError("failed-pre-manifest tombstone run identity is invalid")
    if expected_run is not None:
        require_id(expected_run, "failed tombstone run_id")
    require_id(tombstone.get("attempted_job4_task_id"), "failed tombstone task_id")
    if tombstone.get("provider_calls") != receipt.get("provider_calls"):
        raise CycleError("failed-pre-manifest tombstone provider calls are invalid")
    effects = tombstone.get("effects")
    if not isinstance(effects, dict):
        raise CycleError("failed-pre-manifest effects must be an object")
    exact_keys(
        effects,
        (
            "story_database_writes",
            "route_changes",
            "service_changes",
            "installed_sillytavern_changes",
        ),
        "failed-pre-manifest effects",
    )
    if any(value != 0 or isinstance(value, bool) for value in effects.values()):
        raise CycleError("failed-pre-manifest tombstone contains nonzero product effects")
    if effects != {
        "story_database_writes": receipt.get("story_database_writes"),
        "route_changes": receipt.get("route_changes"),
        "service_changes": receipt.get("service_changes"),
        "installed_sillytavern_changes": receipt.get(
            "installed_sillytavern_changes"
        ),
    }:
        raise CycleError("failed-pre-manifest tombstone effects differ from its receipt")
    if tombstone.get("absent_artifacts") != list(
        FAILED_PRE_MANIFEST_ABSENT_ARTIFACTS
    ):
        raise CycleError("failed-pre-manifest absence inventory is invalid")


def validate_failed_pre_manifest_tombstone(
    root: Path,
    cycle: Path,
    raw: Mapping[str, Any],
    sequence: int,
) -> tuple[dict[str, Any], bytes, bytes]:
    exact_keys(
        raw,
        (
            "cycle_sequence",
            "receipt_copy_path",
            "receipt_copy_sha256",
            "tombstone_path",
            "tombstone_sha256",
        ),
        "failed_pre_manifest_predecessor",
    )
    if raw.get("cycle_sequence") != sequence:
        raise CycleError("failed-pre-manifest predecessor order is invalid")
    for field in ("receipt_copy_path", "tombstone_path"):
        supplied_path = Path(require_string(raw.get(field), field))
        if ".." in supplied_path.parts:
            raise CycleError("failed-pre-manifest custody path contains traversal")
    expected_receipt_name = f"FAILED_PRE_MANIFEST_RECEIPT_{sequence:04d}.json"
    expected_tombstone_name = f"FAILED_PRE_MANIFEST_TOMBSTONE_{sequence:04d}.json"
    receipt_copy, receipt_data, receipt_hash = verified_file(
        root,
        raw.get("receipt_copy_path"),
        raw.get("receipt_copy_sha256"),
        "failed receipt copy",
        suffixes={".json"},
        utf8=True,
    )
    tombstone_path, tombstone_data, tombstone_hash = verified_file(
        root,
        raw.get("tombstone_path"),
        raw.get("tombstone_sha256"),
        "failed tombstone",
        suffixes={".json"},
        utf8=True,
    )
    if (
        receipt_copy.parent != cycle / "source"
        or receipt_copy.name != expected_receipt_name
        or tombstone_path.parent != cycle / "source"
        or tombstone_path.name != expected_tombstone_name
    ):
        raise CycleError("failed-pre-manifest custody files must use exact source names")
    receipt = json.loads(receipt_data.decode("utf-8-sig"))
    tombstone = json.loads(tombstone_data.decode("utf-8-sig"))
    if not isinstance(receipt, dict) or not isinstance(tombstone, dict):
        raise CycleError("failed-pre-manifest custody JSON must contain objects")
    if tombstone_data != canonical_json_bytes(tombstone):
        raise CycleError("failed-pre-manifest tombstone must use canonical JSON")
    _validate_failed_publication_receipt(receipt, sequence)
    _validate_tombstone_semantics(tombstone, receipt, sequence)

    original = repository_relative_file(
        root,
        tombstone["original_failure_receipt_path"],
        "original failed receipt",
        suffixes={".json"},
    )
    original_hash = require_hash(
        tombstone["original_failure_receipt_sha256"],
        "original failed receipt hash",
    )
    if sha256_bytes(original.read_bytes()) != original_hash or original.read_bytes() != receipt_data:
        raise CycleError("failed receipt copy does not match authoritative original bytes")
    source_copy_relative = relative_path(root, receipt_copy)
    if (
        tombstone["source_local_receipt_copy_path"] != source_copy_relative
        or tombstone["source_local_receipt_copy_sha256"] != receipt_hash
    ):
        raise CycleError("failed tombstone does not bind its exact source-local receipt copy")

    spec_path_value, spec_hash_value = _validate_optional_path_hash(
        tombstone["attempted_cycle_spec_path"],
        tombstone["attempted_cycle_spec_sha256"],
        "attempted cycle spec",
    )
    spec: dict[str, Any] | None = None
    if spec_path_value is not None and spec_hash_value is not None:
        attempted_spec = repository_relative_file(
            root, spec_path_value, "attempted cycle spec", suffixes={".json"}
        )
        if sha256_bytes(attempted_spec.read_bytes()) != spec_hash_value:
            raise CycleError("attempted cycle spec hash mismatch")
        spec = read_json(attempted_spec)
        if (
            spec.get("schema_version") not in {SPEC_SCHEMA, SPEC_SCHEMA_V3}
            or spec.get("cycle_id") != tombstone["attempted_cycle_id"]
            or spec.get("cycle_sequence") != sequence
            or not isinstance(spec.get("checkpoint"), dict)
            or spec["checkpoint"].get("id")
            != tombstone["attempted_checkpoint_id"]
            or not isinstance(spec.get("job4"), dict)
            or spec["job4"].get("task_id")
            != tombstone["attempted_job4_task_id"]
        ):
            raise CycleError("attempted cycle spec identity is invalid")
    if receipt.get("attempted_job4_task_id") is not None and (
        tombstone["attempted_job4_task_id"]
        != receipt.get("attempted_job4_task_id")
    ):
        raise CycleError("failed tombstone task identity differs from its receipt")

    auth_path_value, auth_hash_value = _validate_optional_path_hash(
        tombstone["authorization_record_path"],
        tombstone["authorization_record_sha256"],
        "failed authorization record",
    )
    if auth_path_value is not None and auth_hash_value is not None:
        attempted_auth = repository_relative_file(
            root,
            auth_path_value,
            "failed authorization record",
            suffixes={".json"},
        )
        if sha256_bytes(attempted_auth.read_bytes()) != auth_hash_value:
            raise CycleError("failed authorization record hash mismatch")
        if spec is None:
            raise CycleError("failed authorization cannot exist without an attempted spec")
        spec_auth = spec["job4"].get("authorization_record_path")
        spec_auth_hash = spec["job4"].get("authorization_record_sha256")
        resolved_spec_auth = resolve_inside(
            root,
            spec_auth,
            "attempted spec authorization",
            must_exist=True,
            suffixes={".json"},
        )
        if resolved_spec_auth != attempted_auth or spec_auth_hash != auth_hash_value:
            raise CycleError("attempted spec authorization binding is invalid")
    if receipt.get("cycle_spec_sha256") not in {None, spec_hash_value}:
        raise CycleError("failed receipt attempted-spec hash is invalid")
    if receipt.get("source_local_job4_authorization_sha256") not in {
        None,
        auth_hash_value,
    }:
        raise CycleError("failed receipt authorization hash is invalid")

    failed_cycle_id = require_id(
        tombstone["attempted_cycle_id"], "failed tombstone cycle_id"
    )
    failed_cycle = cycle_path(
        root,
        root / ".chatgpt" / "pro-review" / "cycles" / failed_cycle_id,
        failed_cycle_id,
    )
    if not failed_cycle.is_dir():
        raise CycleError("failed cycle directory is missing")
    actual_inventory: list[dict[str, str]] = []
    for item in sorted(
        (candidate for candidate in failed_cycle.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(failed_cycle).as_posix(),
    ):
        if has_link_component(root, item):
            raise CycleError("failed cycle inventory contains a symlink or junction")
        actual_inventory.append(
            {
                "path": item.relative_to(failed_cycle).as_posix(),
                "sha256": sha256_bytes(item.read_bytes()),
            }
        )
    residual = tombstone.get("residual_cycle_inventory")
    if not isinstance(residual, list):
        raise CycleError("failed residual inventory must be a list")
    for index, item in enumerate(residual):
        if not isinstance(item, dict):
            raise CycleError("failed residual inventory entries must be objects")
        exact_keys(item, ("path", "sha256"), f"residual inventory[{index}]")
        require_string(item["path"], f"residual inventory[{index}].path")
        require_hash(item["sha256"], f"residual inventory[{index}].sha256")
    if residual != actual_inventory:
        raise CycleError("failed residual inventory does not match the failed cycle")
    if receipt.get("cycle_directory_empty") is not (not actual_inventory):
        raise CycleError("failed receipt directory-empty claim is invalid")
    if "cycle_source_files" in receipt and receipt["cycle_source_files"] != [
        item["path"] for item in actual_inventory
    ]:
        raise CycleError("failed receipt residual source inventory is invalid")
    for relative in FAILED_PRE_MANIFEST_ABSENT_ARTIFACTS:
        if (failed_cycle / Path(relative)).exists():
            raise CycleError("failed identity contains a forbidden publication artifact")

    public = {
        "cycle_sequence": sequence,
        "cycle_id": failed_cycle_id,
        "checkpoint_id": tombstone["attempted_checkpoint_id"],
        "run_id": tombstone["attempted_run_id"],
        "job4_task_id": tombstone["attempted_job4_task_id"],
        "original_failure_receipt_path": relative_path(root, original),
        "original_failure_receipt_sha256": original_hash,
        "published_receipt_copy_relative_path": f"outbox/{expected_receipt_name}",
        "published_receipt_copy_sha256": receipt_hash,
        "published_tombstone_relative_path": f"outbox/{expected_tombstone_name}",
        "published_tombstone_sha256": tombstone_hash,
        "attempted_cycle_spec_sha256": spec_hash_value,
        "authorization_record_sha256": auth_hash_value,
        "residual_cycle_inventory_sha256": sha256_bytes(
            canonical_json_bytes({"files": actual_inventory})
        ),
    }
    return public, receipt_data, tombstone_data


def validate_failed_pre_manifest_predecessors(
    root: Path,
    cycle: Path,
    value: Any,
    *,
    prior_sequence: int | None,
    current_sequence: int,
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    if not isinstance(value, list):
        raise CycleError("failed_pre_manifest_predecessors must be a list")
    expected = (
        []
        if prior_sequence is None
        else list(range(prior_sequence + 1, current_sequence))
    )
    observed = [
        item.get("cycle_sequence") if isinstance(item, dict) else None
        for item in value
    ]
    if observed != expected:
        raise CycleError("failed-pre-manifest sequence gap is not exact and contiguous")
    public: list[dict[str, Any]] = []
    artifacts: dict[str, bytes] = {}
    for sequence, raw in zip(expected, value):
        if not isinstance(raw, dict):
            raise CycleError("failed-pre-manifest predecessor must be an object")
        item, receipt_data, tombstone_data = validate_failed_pre_manifest_tombstone(
            root, cycle, raw, sequence
        )
        public.append(item)
        artifacts[f"FAILED_PRE_MANIFEST_RECEIPT_{sequence:04d}.json"] = receipt_data
        artifacts[f"FAILED_PRE_MANIFEST_TOMBSTONE_{sequence:04d}.json"] = tombstone_data
    return public, artifacts


def prior_cycle_info(
    root: Path,
    prior_cycle_id: Any,
    sequence: int,
    *,
    require_adjacent: bool = True,
) -> dict[str, Any] | None:
    if prior_cycle_id is None:
        if sequence != 1:
            raise CycleError("every cycle after sequence 1 requires prior_cycle_id")
        return None
    if sequence == 1:
        raise CycleError("bootstrap cycle cannot name a predecessor")
    prior_id = require_id(prior_cycle_id, "prior_cycle_id")
    prior = cycle_path(root, root / ".chatgpt" / "pro-review" / "cycles" / prior_id, prior_id)
    manifest_path = prior / "CYCLE_MANIFEST.json"
    manifest = load_manifest_file(manifest_path)
    prior_sequence = manifest.get("cycle_sequence")
    if (
        manifest.get("cycle_id") != prior_id
        or not isinstance(prior_sequence, int)
        or isinstance(prior_sequence, bool)
        or prior_sequence >= sequence
        or (require_adjacent and prior_sequence != sequence - 1)
    ):
        raise CycleError("prior cycle identity or sequence is invalid")
    if manifest.get("schema_version") in {MANIFEST_SCHEMA, MANIFEST_SCHEMA_V3}:
        completion, completion_hash, consumed_hash, _ = validate_consumed_cycle(
            root, prior, manifest, require_current_source=False
        )
        artifact = resolve_inside(
            root,
            str(prior / completion["job4_result_relative_path"]),
            "prior Job 4 artifact",
            must_exist=True,
        )
        artifact_data = artifact.read_bytes()
        return {
            "cycle_id": prior_id,
            "cycle_sequence": prior_sequence,
            "manifest_sha256": sha256_bytes(manifest_path.read_bytes()),
            "manifest_root_sha256": manifest["manifest_root_sha256"],
            "job4_task_id": completion["job4_task_id"],
            "job4_result_sha256": completion["job4_result_sha256"],
            "job4_completion_receipt_sha256": completion_hash,
            "response_consumption_receipt_sha256": consumed_hash,
            "artifact_source_path": str(artifact),
            "artifact_bytes": artifact_data,
        }

    # The already-preserved cycle 002 uses the legacy receipt format. It is the
    # sole compatibility boundary; every v2 predecessor takes the validated path
    # above.
    completion_path = prior / "receipts" / "JOB4_COMPLETED.json"
    consumption_path = prior / "receipts" / "RESPONSE_CONSUMED.json"
    completion = read_json(completion_path)
    consumption = read_json(consumption_path)
    if (
        completion.get("cycle_id") != prior_id
        or completion.get("event") != "job4_completed"
        or consumption.get("cycle_id") != prior_id
        or consumption.get("event") != "response_consumed"
    ):
        raise CycleError("prior cycle completion/consumption receipts are invalid")
    artifact_relative = completion.get("job4_result_relative_path")
    if not isinstance(artifact_relative, str):
        raise CycleError("prior Job 4 receipt lacks its artifact path")
    artifact = resolve_inside(
        root, str(prior / artifact_relative), "prior Job 4 artifact", must_exist=True
    )
    artifact_data = artifact.read_bytes()
    if sha256_bytes(artifact_data) != completion.get("job4_result_sha256"):
        raise CycleError("prior Job 4 artifact does not match its receipt")
    accepted = prior / ACCEPTED_RESPONSE_RELATIVE_PATH
    if not accepted.is_file() or sha256_bytes(accepted.read_bytes()) != consumption.get("response_sha256"):
        raise CycleError("prior cycle accepted response does not match consumption")
    return {
        "cycle_id": prior_id,
        "cycle_sequence": prior_sequence,
        "manifest_sha256": sha256_bytes(manifest_path.read_bytes()),
        "manifest_root_sha256": manifest.get(
            "manifest_root_sha256", sha256_bytes(manifest_path.read_bytes())
        ),
        "job4_task_id": completion.get("job4_task_id"),
        "job4_result_sha256": completion["job4_result_sha256"],
        "job4_completion_receipt_sha256": sha256_bytes(completion_path.read_bytes()),
        "response_consumption_receipt_sha256": sha256_bytes(consumption_path.read_bytes()),
        "artifact_source_path": str(artifact),
        "artifact_bytes": artifact_data,
    }


def validate_spec(
    root: Path, cycle: Path, spec: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, bytes]]:
    schema = spec.get("schema_version")
    if schema == SPEC_SCHEMA:
        spec_fields = (
            "schema_version",
            "cycle_id",
            "cycle_sequence",
            "checkpoint",
            "jobs_1_3",
            "prior_cycle_id",
            "job4",
            "review_context",
            "review_snapshot",
        )
    elif schema == SPEC_SCHEMA_V3:
        spec_fields = (
            "schema_version",
            "cycle_id",
            "cycle_sequence",
            "checkpoint",
            "jobs_1_3",
            "prior_cycle_id",
            "failed_pre_manifest_predecessors",
            "job4",
            "review_context",
            "review_snapshot",
        )
    else:
        raise CycleError("unsupported cycle spec schema")
    exact_keys(
        spec,
        spec_fields,
        "cycle spec",
    )
    cycle_id = require_id(spec["cycle_id"], "cycle_id")
    cycle_path(root, cycle, cycle_id)
    sequence = spec["cycle_sequence"]
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise CycleError("cycle_sequence must be a positive integer")

    checkpoint = spec["checkpoint"]
    if not isinstance(checkpoint, dict):
        raise CycleError("checkpoint must be an object")
    exact_keys(checkpoint, ("id", "git_sha", "evidence_path", "evidence_sha256"), "checkpoint")
    checkpoint_id = require_id(checkpoint["id"], "checkpoint.id")
    checkpoint_git_sha = verify_git_object(root, checkpoint["git_sha"], "checkpoint.git_sha")
    evidence_path, evidence_data, evidence_sha = verified_file(
        root,
        checkpoint["evidence_path"],
        checkpoint["evidence_sha256"],
        "checkpoint.evidence",
        suffixes={".zip"},
    )
    validate_zip(evidence_data, "checkpoint evidence")

    raw_jobs = spec["jobs_1_3"]
    if not isinstance(raw_jobs, list) or not 1 <= len(raw_jobs) <= 3:
        raise CycleError("jobs_1_3 must contain one through three named results")
    jobs: list[dict[str, str]] = []
    task_ids: set[str] = set()
    artifacts: dict[str, bytes] = {"CHECKPOINT_EVIDENCE.zip": evidence_data}
    for index, raw_job in enumerate(raw_jobs, start=1):
        job, data = task_result(root, raw_job, f"jobs_1_3[{index - 1}]")
        if job["task_id"] in task_ids:
            raise CycleError("progression task identities must be unique")
        task_ids.add(job["task_id"])
        jobs.append(job)
        artifacts[f"JOB_{index}_RESULT.md"] = data

    prior = prior_cycle_info(
        root,
        spec["prior_cycle_id"],
        sequence,
        require_adjacent=schema == SPEC_SCHEMA,
    )
    failed_pre_manifest: list[dict[str, Any]] = []
    if schema == SPEC_SCHEMA_V3:
        failed_pre_manifest, failed_artifacts = (
            validate_failed_pre_manifest_predecessors(
                root,
                cycle,
                spec["failed_pre_manifest_predecessors"],
                prior_sequence=None if prior is None else prior["cycle_sequence"],
                current_sequence=sequence,
            )
        )
        artifacts.update(failed_artifacts)
    if prior is not None:
        if prior["job4_task_id"] in task_ids:
            raise CycleError("prior Job 4 must be distinct from current progressions")
        artifacts["PRIOR_JOB4_RESULT" + Path(prior["artifact_source_path"]).suffix] = prior[
            "artifact_bytes"
        ]

    job4 = spec["job4"]
    if not isinstance(job4, dict):
        raise CycleError("job4 must be an object")
    exact_keys(
        job4,
        ("task_id", "scope", "authorization_record_path", "authorization_record_sha256"),
        "job4",
    )
    job4_id = require_id(job4["task_id"], "job4.task_id")
    if job4_id in task_ids or (prior and job4_id == prior["job4_task_id"]):
        raise CycleError("Job 4 identity must be distinct")
    scope = require_string(job4["scope"], "job4.scope", min_length=20)
    authorization, authorization_data, authorization_hash = validate_authorization(
        root,
        cycle,
        cycle_id,
        job4_id,
        scope,
        job4["authorization_record_path"],
        job4["authorization_record_sha256"],
    )
    artifacts["JOB4_AUTHORIZATION.json"] = authorization_data

    review_context = validate_review_context(spec["review_context"], len(jobs))
    source_manifest, source_manifest_bytes, source_archive = build_snapshot(
        root, cycle_id, spec["review_snapshot"], checkpoint_git_sha
    )
    artifacts["CHANGED_SOURCE_MANIFEST.json"] = source_manifest_bytes
    artifacts["SOURCE_SNAPSHOT.zip"] = source_archive

    prior_public = None if prior is None else {
        key: value for key, value in prior.items() if key not in {"artifact_source_path", "artifact_bytes"}
    }
    task_payload = {
        "cycle_id": cycle_id,
        "cycle_sequence": sequence,
        "checkpoint_id": checkpoint_id,
        "checkpoint_git_sha": checkpoint_git_sha,
        "evidence_sha256": evidence_sha,
        "source_root_sha256": source_manifest["source_root_sha256"],
        "review_context_sha256": sha256_bytes(canonical_json_bytes(review_context)),
        "jobs_1_3": [
            {
                "task_id": item["task_id"],
                "status": item["status"],
                "sha256": item["sha256"],
            }
            for item in jobs
        ],
        "prior_cycle": prior_public,
        "job4": {
            "task_id": job4_id,
            "scope_sha256": sha256_text(scope),
            "authorization_record_sha256": authorization_hash,
            "expected_result_relative_path": authorization["expected_result_relative_path"],
        },
    }
    if schema == SPEC_SCHEMA_V3:
        task_payload["failed_pre_manifest_predecessors"] = failed_pre_manifest
    task_set_hash = sha256_bytes(canonical_json_bytes(task_payload))
    nonce_version = "v3" if schema == SPEC_SCHEMA_V3 else "v2"
    nonce = sha256_text(
        f"cera.pro_review_response_nonce.{nonce_version}\0{task_set_hash}"
    )
    unsigned_manifest: dict[str, Any] = {
        "schema_version": (
            MANIFEST_SCHEMA_V3 if schema == SPEC_SCHEMA_V3 else MANIFEST_SCHEMA
        ),
        "cycle_id": cycle_id,
        "cycle_sequence": sequence,
        "repository_identity_sha256": sha256_text(str(root).casefold()),
        "checkpoint": {
            "id": checkpoint_id,
            "git_sha": checkpoint_git_sha,
            "evidence_source_path": str(evidence_path),
            "evidence_sha256": evidence_sha,
        },
        "source_snapshot": source_manifest,
        "review_context": review_context,
        "jobs_1_3": jobs,
        "prior_cycle": prior_public,
        "job4": {
            "task_id": job4_id,
            "scope": scope,
            "scope_sha256": sha256_text(scope),
            "authorization_record_path": str(cycle / "outbox" / "JOB4_AUTHORIZATION.json"),
            "authorization_record_sha256": authorization_hash,
            "expected_result_relative_path": authorization["expected_result_relative_path"],
        },
        "task_set_sha256": task_set_hash,
        "response_nonce": nonce,
        "expected_response_relative_path": EXPECTED_RESPONSE_RELATIVE_PATH.as_posix(),
        "accepted_response_relative_path": ACCEPTED_RESPONSE_RELATIVE_PATH.as_posix(),
    }
    if schema == SPEC_SCHEMA_V3:
        unsigned_manifest["failed_pre_manifest_predecessors"] = failed_pre_manifest
    manifest_root = sha256_bytes(canonical_json_bytes(unsigned_manifest))
    manifest = {**unsigned_manifest, "manifest_root_sha256": manifest_root}
    return manifest, artifacts


def request_markdown(manifest: Mapping[str, Any]) -> bytes:
    checkpoint = manifest["checkpoint"]
    context = manifest["review_context"]
    lines = [
        "# CERA Repository Review Cycle",
        "",
        f"review_cycle_id: {manifest['cycle_id']}",
        f"checkpoint_id: {checkpoint['id']}",
        f"checkpoint_git_sha: {checkpoint['git_sha']}",
        f"evidence_sha256: {checkpoint['evidence_sha256']}",
        f"source_root_sha256: {manifest['source_snapshot']['source_root_sha256']}",
        f"manifest_root_sha256: {manifest['manifest_root_sha256']}",
        f"task_set_sha256: {manifest['task_set_sha256']}",
        f"job4_task_id: {manifest['job4']['task_id']}",
        f"response_nonce: {manifest['response_nonce']}",
        f"expected_response_path: {manifest['expected_response_relative_path']}",
        "",
        "## Creator goal",
        "",
        context["creator_goal"],
        "",
        "## Current or revised progressions",
        "",
    ]
    for index, job in enumerate(manifest["jobs_1_3"], start=1):
        lines.append(
            f"- Job {index}: `{job['task_id']}`; `JOB_{index}_RESULT.md`; "
            f"SHA-256 `{job['sha256']}`; rationale: {context['selection_rationale'][index - 1]}"
        )
    lines.extend(["", "## Preceding Job 4 provenance", ""])
    prior = manifest["prior_cycle"]
    if prior is None:
        lines.append("- Bootstrap cycle: no predecessor.")
    else:
        lines.extend(
            [
                f"- Prior cycle: `{prior['cycle_id']}` sequence {prior['cycle_sequence']}.",
                f"- Prior manifest root: `{prior['manifest_root_sha256']}`.",
                f"- Prior Job 4: `{prior['job4_task_id']}` result `{prior['job4_result_sha256']}`.",
                f"- Completion receipt: `{prior['job4_completion_receipt_sha256']}`.",
                f"- Consumption receipt: `{prior['response_consumption_receipt_sha256']}`.",
            ]
        )
    if manifest["schema_version"] == MANIFEST_SCHEMA_V3:
        lines.extend(["", "## Failed pre-manifest sequence custody", ""])
        failed = manifest["failed_pre_manifest_predecessors"]
        if not failed:
            lines.append("- Direct consumed predecessor: no failed sequence gap.")
        else:
            for item in failed:
                lines.append(
                    f"- Failed sequence {item['cycle_sequence']}: "
                    f"`{item['cycle_id']}`; receipt "
                    f"`{item['published_receipt_copy_sha256']}`; tombstone "
                    f"`{item['published_tombstone_sha256']}`."
                )
    lines.extend(
        [
            "",
            "## Concurrent pre-authorized Job 4",
            "",
            f"- Task: `{manifest['job4']['task_id']}`",
            f"- Scope: {manifest['job4']['scope']}",
            f"- Structured authorization: `{manifest['job4']['authorization_record_sha256']}`.",
            "",
            "## Bound source and evidence",
            "",
            "- `CHANGED_SOURCE_MANIFEST.json` lists every nonexcluded Git-status path.",
            "- `SOURCE_SNAPSHOT.zip` contains the exact listed bytes.",
            f"- Starting baseline: {context['starting_baseline_or_prior_checkpoint']}",
            f"- Diff summary: {context['diff_summary']}",
            f"- Focused tests: {context['focused_tests']}",
            f"- Complete suite: {context['complete_suite']}",
            f"- Active profile before: {context['active_profile_before']}",
            f"- Active profile after: {context['active_profile_after']}",
            f"- Provider/cost effects: {context['provider_calls_and_cost']}",
            f"- Retry/fallback: {context['retry_and_fallback']}",
            f"- Story/database/branch effects: {context['story_database_and_branch_effects']}",
            f"- User-visible effect: {context['user_visible_effect']}",
            f"- Historical integrity: {context['historical_evidence_integrity']}",
            f"- Unresolved defects: {context['unresolved_defects']}",
            f"- Uncertainty/risks: {context['uncertainty_and_risks']}",
            f"- Prior-review disagreement: {context['disagreement_with_prior_review']}",
            "- Advisory candidates: " + "; ".join(context["codex_advisory_next_candidates"]),
            "- Questions for Pro: " + "; ".join(context["questions_for_chatgpt_pro"]),
            "- Explicit exclusions: " + "; ".join(context["explicit_exclusions"]),
            "",
            "## Required response identity",
            "",
            "Write atomically to the exact response path with this block:",
            "",
            "```yaml",
            f"review_cycle_id: {manifest['cycle_id']}",
            f"reviewed_checkpoint_id: {checkpoint['id']}",
            f"reviewed_checkpoint_git_sha: {checkpoint['git_sha']}",
            f"reviewed_evidence_sha256: {checkpoint['evidence_sha256']}",
            f"reviewed_task_set_sha256: {manifest['task_set_sha256']}",
            f"reviewed_job4_task_id: {manifest['job4']['task_id']}",
            f"response_nonce: {manifest['response_nonce']}",
            "review_scope: repository_cycle",
            "review_disposition: accepted | corrections_required | blocked",
            "```",
            "",
            "Complete all five planning sections: `## Independent findings`, "
            "`## Required corrections`, `## Next three progressions`, "
            "`## Recommended next Job 4`, and `## Explicitly not authorized`. "
            "The response remains advisory and grants no creator authority.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def response_template(manifest: Mapping[str, Any]) -> bytes:
    checkpoint = manifest["checkpoint"]
    return (
        "# CERA ChatGPT Pro Repository Review\n\n"
        f"review_cycle_id: {manifest['cycle_id']}\n"
        f"reviewed_checkpoint_id: {checkpoint['id']}\n"
        f"reviewed_checkpoint_git_sha: {checkpoint['git_sha']}\n"
        f"reviewed_evidence_sha256: {checkpoint['evidence_sha256']}\n"
        f"reviewed_task_set_sha256: {manifest['task_set_sha256']}\n"
        f"reviewed_job4_task_id: {manifest['job4']['task_id']}\n"
        f"response_nonce: {manifest['response_nonce']}\n"
        "review_scope: repository_cycle\n"
        "review_disposition: accepted | corrections_required | blocked\n\n"
        "## Independent findings\n\n"
        "REPLACE THIS PLACEHOLDER WITH THE COMPLETED EVIDENCE-BACKED REVIEW.\n\n"
        "## Required corrections\n\n"
        "REPLACE THIS PLACEHOLDER WITH REQUIRED CORRECTIONS OR AN EXPLICIT NONE.\n\n"
        "## Next three progressions\n\n"
        "REPLACE THIS PLACEHOLDER WITH ONE TO THREE ADVISORY PROGRESSIONS OR AN EXPLICIT NONE.\n\n"
        "## Recommended next Job 4\n\n"
        "REPLACE THIS PLACEHOLDER WITH THE ADVISORY NEXT JOB 4 OR AN EXPLICIT NONE.\n\n"
        "## Explicitly not authorized\n\n"
        "REPLACE THIS PLACEHOLDER WITH THE EXCLUSIONS THAT REMAIN CLOSED.\n"
    ).encode("utf-8")


def trigger_message(manifest: Mapping[str, Any], cycle: Path) -> bytes:
    return (
        f"CERA repository review cycle {manifest['cycle_id']} is published and "
        "its exact pre-authorized Job 4 has started.\n\n"
        f"Review: {cycle / 'outbox' / 'REVIEW_REQUEST.md'}\n"
        f"Manifest root: {manifest['manifest_root_sha256']}\n"
        f"Task-set SHA-256: {manifest['task_set_sha256']}\n"
        f"Response target: {cycle / EXPECTED_RESPONSE_RELATIVE_PATH}\n\n"
        "Inspect the bound source snapshot and repository files, then write the "
        "identity-bound response atomically through the repository connector. "
        "Do not ask Ted to relay or operate files. The review is advisory.\n"
    ).encode("utf-8")


def make_receipt(
    manifest: Mapping[str, Any],
    event: str,
    predecessor_sha256: str | None,
    details: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": RECEIPT_SCHEMA,
        "cycle_id": manifest["cycle_id"],
        "event": event,
        "manifest_root_sha256": manifest["manifest_root_sha256"],
        "predecessor_receipt_sha256": predecessor_sha256,
        "recorded_at_utc": utc_now(),
        **details,
    }


def validate_receipt(
    path: Path,
    manifest: Mapping[str, Any],
    event: str,
    predecessor_sha256: str | None,
) -> tuple[dict[str, Any], str]:
    value = read_json(path)
    event_fields = RECEIPT_EVENT_FIELDS.get(event)
    if event_fields is None:
        raise CycleError(f"unsupported chained receipt event: {event}")
    exact_keys(value, RECEIPT_COMMON_FIELDS + event_fields, f"{event} receipt")
    if (
        value.get("schema_version") != RECEIPT_SCHEMA
        or value.get("cycle_id") != manifest["cycle_id"]
        or value.get("event") != event
        or value.get("manifest_root_sha256") != manifest["manifest_root_sha256"]
        or value.get("predecessor_receipt_sha256") != predecessor_sha256
    ):
        raise CycleError(f"receipt chain validation failed: {path}")
    require_string(value["recorded_at_utc"], "receipt.recorded_at_utc")
    return value, sha256_bytes(path.read_bytes())


def state_value(
    manifest: Mapping[str, Any], state: str, receipt_name: str, receipt_hash: str
) -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA,
        "cycle_id": manifest["cycle_id"],
        "state": state,
        "manifest_root_sha256": manifest["manifest_root_sha256"],
        "checkpoint_id": manifest["checkpoint"]["id"],
        "checkpoint_git_sha": manifest["checkpoint"]["git_sha"],
        "evidence_sha256": manifest["checkpoint"]["evidence_sha256"],
        "task_set_sha256": manifest["task_set_sha256"],
        "job4_task_id": manifest["job4"]["task_id"],
        "last_receipt": receipt_name,
        "last_receipt_sha256": receipt_hash,
        "updated_at_utc": utc_now(),
    }


def validate_state_view(
    cycle: Path,
    manifest: Mapping[str, Any],
    expected_state: str,
    receipt_name: str,
    receipt_hash: str,
) -> dict[str, Any]:
    state = read_json(cycle / "state" / "CYCLE_STATE.json")
    exact_keys(
        state,
        (
            "schema_version",
            "cycle_id",
            "state",
            "manifest_root_sha256",
            "checkpoint_id",
            "checkpoint_git_sha",
            "evidence_sha256",
            "task_set_sha256",
            "job4_task_id",
            "last_receipt",
            "last_receipt_sha256",
            "updated_at_utc",
        ),
        "cycle state",
    )
    expected = state_value(manifest, expected_state, receipt_name, receipt_hash)
    expected.pop("updated_at_utc")
    observed = {key: value for key, value in state.items() if key != "updated_at_utc"}
    if observed != expected:
        raise CycleError("mutable state view does not match the validated receipt chain")
    return state


def validate_trigger_receipt(
    cycle: Path,
    manifest: Mapping[str, Any],
    started_hash: str,
) -> tuple[dict[str, Any], str]:
    receipt, digest = validate_receipt(
        cycle / "receipts" / "TRIGGER_SENT.json",
        manifest,
        "review_trigger_sent",
        started_hash,
    )
    message_hash = sha256_bytes((cycle / "outbox" / "TRIGGER_MESSAGE.txt").read_bytes())
    if (
        receipt["transport"] != "codex_app_send_message_to_thread"
        or receipt["message_sha256"] != message_hash
        or receipt["app_result_attested_success"] is not True
        or receipt["independent_delivery_proof"] is not False
        or receipt["raw_target_message_or_app_result_retained"] is not False
    ):
        raise CycleError("trigger receipt claims do not match the transport contract")
    require_hash(receipt["target_id_sha256"], "trigger target hash")
    require_hash(receipt["app_result_sha256"], "trigger app result hash")
    return receipt, digest


def load_v2_manifest(root: Path, cycle: Path) -> dict[str, Any]:
    cycle_path(root, cycle)
    manifest = load_manifest_file(cycle / "CYCLE_MANIFEST.json")
    if manifest.get("schema_version") not in {
        MANIFEST_SCHEMA,
        MANIFEST_SCHEMA_V3,
    }:
        raise CycleError("legacy cycle is immutable and cannot use modern transitions")
    cycle_path(root, cycle, manifest["cycle_id"])
    if manifest.get("repository_identity_sha256") != sha256_text(str(root).casefold()):
        raise CycleError("cycle belongs to a different repository")
    return manifest


def _failed_public_fields() -> tuple[str, ...]:
    return (
        "cycle_sequence",
        "cycle_id",
        "checkpoint_id",
        "run_id",
        "job4_task_id",
        "original_failure_receipt_path",
        "original_failure_receipt_sha256",
        "published_receipt_copy_relative_path",
        "published_receipt_copy_sha256",
        "published_tombstone_relative_path",
        "published_tombstone_sha256",
        "attempted_cycle_spec_sha256",
        "authorization_record_sha256",
        "residual_cycle_inventory_sha256",
    )


def validate_published_failed_pre_manifest_chain(
    root: Path, cycle: Path, manifest: Mapping[str, Any]
) -> None:
    if manifest.get("schema_version") != MANIFEST_SCHEMA_V3:
        return
    prior = manifest.get("prior_cycle")
    prior_sequence = None if prior is None else prior.get("cycle_sequence")
    sequence = manifest.get("cycle_sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool):
        raise CycleError("V3 cycle sequence is invalid")
    expected = (
        []
        if prior_sequence is None
        else list(range(prior_sequence + 1, sequence))
    )
    failed = manifest.get("failed_pre_manifest_predecessors")
    if not isinstance(failed, list) or [
        item.get("cycle_sequence") if isinstance(item, dict) else None
        for item in failed
    ] != expected:
        raise CycleError("published failed-pre-manifest chain is not exact and contiguous")
    for expected_sequence, item in zip(expected, failed):
        if not isinstance(item, dict):
            raise CycleError("published failed-pre-manifest entry must be an object")
        exact_keys(item, _failed_public_fields(), "published failed-pre-manifest entry")
        receipt_name = f"FAILED_PRE_MANIFEST_RECEIPT_{expected_sequence:04d}.json"
        tombstone_name = f"FAILED_PRE_MANIFEST_TOMBSTONE_{expected_sequence:04d}.json"
        expected_receipt_relative = f"outbox/{receipt_name}"
        expected_tombstone_relative = f"outbox/{tombstone_name}"
        if (
            item["published_receipt_copy_relative_path"]
            != expected_receipt_relative
            or item["published_tombstone_relative_path"]
            != expected_tombstone_relative
        ):
            raise CycleError("published failed-pre-manifest paths are invalid")
        receipt_path = resolve_inside(
            root,
            str(cycle / expected_receipt_relative),
            "published failed receipt",
            must_exist=True,
            suffixes={".json"},
        )
        tombstone_path = resolve_inside(
            root,
            str(cycle / expected_tombstone_relative),
            "published failed tombstone",
            must_exist=True,
            suffixes={".json"},
        )
        receipt_data = receipt_path.read_bytes()
        tombstone_data = tombstone_path.read_bytes()
        if (
            sha256_bytes(receipt_data)
            != item["published_receipt_copy_sha256"]
            or sha256_bytes(tombstone_data)
            != item["published_tombstone_sha256"]
        ):
            raise CycleError("published failed-pre-manifest custody hash mismatch")
        receipt = json.loads(receipt_data.decode("utf-8-sig"))
        tombstone = json.loads(tombstone_data.decode("utf-8-sig"))
        if not isinstance(receipt, dict) or not isinstance(tombstone, dict):
            raise CycleError("published failed-pre-manifest custody must contain objects")
        if tombstone_data != canonical_json_bytes(tombstone):
            raise CycleError("published failed-pre-manifest tombstone is not canonical")
        _validate_failed_publication_receipt(receipt, expected_sequence)
        _validate_tombstone_semantics(tombstone, receipt, expected_sequence)
        residual = tombstone["residual_cycle_inventory"]
        if not isinstance(residual, list):
            raise CycleError("published failed residual inventory must be a list")
        residual_hash = sha256_bytes(canonical_json_bytes({"files": residual}))
        if (
            item["cycle_id"] != tombstone["attempted_cycle_id"]
            or item["checkpoint_id"] != tombstone["attempted_checkpoint_id"]
            or item["run_id"] != tombstone["attempted_run_id"]
            or item["job4_task_id"] != tombstone["attempted_job4_task_id"]
            or item["original_failure_receipt_path"]
            != tombstone["original_failure_receipt_path"]
            or item["original_failure_receipt_sha256"]
            != tombstone["original_failure_receipt_sha256"]
            or item["published_receipt_copy_sha256"]
            != tombstone["source_local_receipt_copy_sha256"]
            or item["attempted_cycle_spec_sha256"]
            != tombstone["attempted_cycle_spec_sha256"]
            or item["authorization_record_sha256"]
            != tombstone["authorization_record_sha256"]
            or item["residual_cycle_inventory_sha256"] != residual_hash
        ):
            raise CycleError("published failed-pre-manifest manifest binding is invalid")


def validate_snapshot_archive(cycle: Path, manifest: Mapping[str, Any]) -> None:
    source = manifest["source_snapshot"]
    manifest_path = cycle / "outbox" / "CHANGED_SOURCE_MANIFEST.json"
    if read_json(manifest_path) != source:
        raise CycleError("published source manifest differs from the cycle manifest")
    archive_path = cycle / "outbox" / "SOURCE_SNAPSHOT.zip"
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            expected_names = {
                f"files/{entry['path']}"
                for entry in source["included_changes"]
                if entry["content_state"] == "present"
            }
            if set(archive.namelist()) != expected_names or archive.testzip() is not None:
                raise CycleError("published source archive inventory is invalid")
            if (
                source["archived_file_count"] != len(expected_names)
                or source["archived_file_count"] > source["file_count_ceiling"]
                or source["archived_total_bytes"] > source["total_byte_ceiling"]
            ):
                raise CycleError("published source archive limits are invalid")
            observed_total = 0
            for entry in source["included_changes"]:
                if entry["content_state"] == "deleted":
                    if entry["sha256"] is not None or entry["size"] is not None:
                        raise CycleError("source tombstone contains impossible content metadata")
                    continue
                data = archive.read(f"files/{entry['path']}")
                observed_total += len(data)
                if len(data) != entry["size"] or sha256_bytes(data) != entry["sha256"]:
                    raise CycleError("published source archive content hash mismatch")
            if observed_total != source["archived_total_bytes"]:
                raise CycleError("published source archive total byte count is invalid")
    except zipfile.BadZipFile as exc:
        raise CycleError("published source archive is corrupt") from exc


def validate_snapshot_current(root: Path, cycle: Path, manifest: Mapping[str, Any]) -> None:
    source = manifest["source_snapshot"]
    changed = git_status_changes(root)
    expected_included = source["included_changes"]
    expected_excluded = source["excluded_status_changes"]
    current_included: list[dict[str, Any]] = []
    current_excluded: list[dict[str, Any]] = []
    exclusions = snapshot_exclusions(manifest["cycle_id"])
    for change in changed:
        paths = [str(change["path"])]
        if change["original_path"] is not None:
            paths.append(str(change["original_path"]))
        matches = [
            next(
                (item for item in exclusions if path.startswith(item["path_prefix"])),
                None,
            )
            for path in paths
        ]
        if any(item is not None for item in matches):
            if not all(item == matches[0] for item in matches):
                raise CycleError("Git change crosses a snapshot exclusion boundary")
            current_excluded.append({**change, "reason_code": matches[0]["reason_code"]})
        else:
            relative = str(change["path"])
            candidate = root / relative
            present = candidate.exists()
            if present:
                path = resolve_inside(
                    root, str(candidate), "reviewed source", must_exist=True
                )
                data = path.read_bytes()
                digest: str | None = sha256_bytes(data)
                size: int | None = len(data)
                content_state = "present"
            else:
                resolve_inside(
                    root, str(candidate), "reviewed source deletion", must_exist=False
                )
                digest = None
                size = None
                content_state = "deleted"
            current_included.append(
                {
                    "status": change["status"],
                    "path": relative,
                    "original_path": change["original_path"],
                    "content_state": content_state,
                    "sha256": digest,
                    "size": size,
                }
            )
    if current_included != expected_included:
        raise CycleError("repository changed-file inventory drifted after publication")
    # New files inside current-cycle transport state are expected; connector metadata
    # may also change. Previously excluded paths must remain in their allowed prefixes.
    expected_excluded_keys = {
        canonical_json_bytes(item) for item in expected_excluded
    }
    current_excluded_keys = {
        canonical_json_bytes(item) for item in current_excluded
    }
    if not expected_excluded_keys.issubset(current_excluded_keys):
        raise CycleError("declared exclusion inventory was removed or changed")


def validate_publication(
    root: Path,
    cycle: Path,
    manifest: Mapping[str, Any],
    *,
    require_current_source: bool = True,
) -> tuple[str, str]:
    published_path = cycle / "receipts" / "PUBLISHED.json"
    published, published_hash = validate_receipt(published_path, manifest, "jobs_1_3_published", None)
    outbox_hashes = published["outbox_sha256"]
    if not isinstance(outbox_hashes, dict):
        raise CycleError("publication receipt outbox map must be an object")
    actual_names = {
        path.name for path in (cycle / "outbox").iterdir() if path.is_file()
    }
    if set(outbox_hashes) != actual_names:
        raise CycleError("publication receipt does not bind the exact outbox inventory")
    for name, digest in outbox_hashes.items():
        require_hash(digest, f"published outbox hash {name}")
        path = cycle / "outbox" / name
        if not path.is_file() or sha256_bytes(path.read_bytes()) != digest:
            raise CycleError(f"published outbox artifact changed: {name}")
    if (
        published["task_set_sha256"] != manifest["task_set_sha256"]
        or published["source_root_sha256"]
        != manifest["source_snapshot"]["source_root_sha256"]
        or published["package_publication"]
        != "individually_atomic_with_published_commit_marker"
    ):
        raise CycleError("publication receipt identity fields do not match the manifest")
    validate_published_failed_pre_manifest_chain(root, cycle, manifest)
    validate_snapshot_archive(cycle, manifest)
    started_path = cycle / "receipts" / "JOB4_STARTED.json"
    started, started_hash = validate_receipt(
        started_path, manifest, "job4_started", published_hash
    )
    if (
        started.get("job4_task_id") != manifest["job4"]["task_id"]
        or started.get("job4_scope_sha256") != manifest["job4"]["scope_sha256"]
        or started.get("authorization_record_sha256")
        != manifest["job4"]["authorization_record_sha256"]
        or started.get("authorization_record_contract_validated") is not True
        or started.get("unrelated_work_authorized") is not False
    ):
        raise CycleError("Job 4 start receipt does not match the manifest")
    if require_current_source:
        validate_snapshot_current(root, cycle, manifest)
    return published_hash, started_hash


def publish_cycle(
    cycle_directory: Path, spec_path: Path, *, repository_root_path: Path | None = None
) -> dict[str, Any]:
    root = repository_root(repository_root_path)
    spec = read_json(spec_path.resolve(strict=True))
    cycle_id = require_id(spec.get("cycle_id"), "cycle_id")
    cycle = cycle_path(root, cycle_directory, cycle_id)
    cycle.mkdir(parents=True, exist_ok=True)
    manifest, artifacts = validate_spec(root, cycle, spec)
    immutable_write(cycle / "CYCLE_MANIFEST.json", canonical_json_bytes(manifest))
    outbox = cycle / "outbox"
    for name, data in artifacts.items():
        immutable_write(outbox / name, data)
    immutable_write(outbox / "REVIEW_REQUEST.md", request_markdown(manifest))
    immutable_write(outbox / "PRO_RESPONSE_TEMPLATE.md", response_template(manifest))
    immutable_write(outbox / "TRIGGER_MESSAGE.txt", trigger_message(manifest, cycle))
    outbox_hashes = {
        path.name: sha256_bytes(path.read_bytes()) for path in sorted(outbox.iterdir()) if path.is_file()
    }
    published = make_receipt(
        manifest,
        "jobs_1_3_published",
        None,
        {
            "task_set_sha256": manifest["task_set_sha256"],
            "source_root_sha256": manifest["source_snapshot"]["source_root_sha256"],
            "outbox_sha256": outbox_hashes,
            "package_publication": "individually_atomic_with_published_commit_marker",
        },
    )
    published_path = cycle / "receipts" / "PUBLISHED.json"
    receipt_write(published_path, published)
    published_hash = sha256_bytes(published_path.read_bytes())
    started = make_receipt(
        manifest,
        "job4_started",
        published_hash,
        {
            "job4_task_id": manifest["job4"]["task_id"],
            "job4_scope_sha256": manifest["job4"]["scope_sha256"],
            "authorization_record_sha256": manifest["job4"]["authorization_record_sha256"],
            "authorization_record_contract_validated": True,
            "unrelated_work_authorized": False,
        },
    )
    started_path = cycle / "receipts" / "JOB4_STARTED.json"
    receipt_write(started_path, started)
    started_hash = sha256_bytes(started_path.read_bytes())
    state = state_value(manifest, STATE_JOB4_IN_PROGRESS, "JOB4_STARTED.json", started_hash)
    existing_state_path = cycle / "state" / "CYCLE_STATE.json"
    if existing_state_path.exists():
        return recover_cycle(cycle, repository_root_path=root)
    atomic_replace(existing_state_path, canonical_json_bytes(state))
    return state


def record_trigger(
    cycle_directory: Path,
    *,
    target_id: str,
    app_result_json: str,
    message_sha256: str,
    repository_root_path: Path | None = None,
) -> dict[str, Any]:
    root = repository_root(repository_root_path)
    cycle = cycle_path(root, cycle_directory)
    manifest = load_v2_manifest(root, cycle)
    _, started_hash = validate_publication(root, cycle, manifest)
    target_id = require_string(target_id, "target_id")
    message_hash = require_hash(message_sha256, "message_sha256")
    message = cycle / "outbox" / "TRIGGER_MESSAGE.txt"
    if sha256_bytes(message.read_bytes()) != message_hash:
        raise CycleError("trigger message hash does not match the published message")
    try:
        app_result = json.loads(app_result_json)
    except json.JSONDecodeError as exc:
        raise CycleError("app result is not valid JSON") from exc
    if not isinstance(app_result, dict) or app_result.get("threadId") != target_id:
        raise CycleError("app result does not confirm the exact target thread")
    if app_result.get("error"):
        raise CycleError("app result reports an error")
    target_hash = sha256_text(target_id)
    app_result_hash = sha256_text(app_result_json)
    path = cycle / "receipts" / "TRIGGER_SENT.json"
    if path.exists():
        existing, _ = validate_trigger_receipt(cycle, manifest, started_hash)
        if (
            existing["target_id_sha256"] == target_hash
            and existing["message_sha256"] == message_hash
            and existing["app_result_sha256"] == app_result_hash
        ):
            return existing
        raise CycleError("conflicting review trigger attestation already exists")
    if (cycle / "receipts" / "JOB4_COMPLETED.json").exists():
        raise CycleError("review trigger cannot be recorded after Job 4 completion")
    validate_state_view(
        cycle,
        manifest,
        STATE_JOB4_IN_PROGRESS,
        "JOB4_STARTED.json",
        started_hash,
    )
    receipt = make_receipt(
        manifest,
        "review_trigger_sent",
        started_hash,
        {
            "transport": "codex_app_send_message_to_thread",
            "target_id_sha256": target_hash,
            "message_sha256": message_hash,
            "app_result_sha256": app_result_hash,
            "app_result_attested_success": True,
            "independent_delivery_proof": False,
            "raw_target_message_or_app_result_retained": False,
        },
    )
    result = receipt_write(path, receipt)
    receipt_hash = sha256_bytes(path.read_bytes())
    state = state_value(
        manifest, STATE_JOB4_IN_PROGRESS, "TRIGGER_SENT.json", receipt_hash
    )
    atomic_replace(cycle / "state" / "CYCLE_STATE.json", canonical_json_bytes(state))
    return result


def validate_job4_result_contract(
    value: Any, manifest: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CycleError("Job 4 result must be an object")
    schema_version = value.get("schema_version")
    fields = [
        "schema_version",
        "cycle_id",
        "task_id",
        "status",
        "report_relative_path",
        "report_sha256",
        "effects",
        "verification",
    ]
    if schema_version in {JOB4_RESULT_SCHEMA_V2, JOB4_RESULT_SCHEMA_V3}:
        fields.extend(
            ("terminal_evidence_relative_path", "terminal_evidence_sha256")
        )
    if schema_version == JOB4_RESULT_SCHEMA_V3:
        fields.extend(
            (
                "test_diagnostics",
                "test_diagnostics_sha256",
                "test_records_root_sha256",
                "test_record_count",
            )
        )
    exact_keys(value, fields, "Job 4 result")
    if (
        schema_version
        not in {
            JOB4_RESULT_SCHEMA,
            JOB4_RESULT_SCHEMA_V2,
            JOB4_RESULT_SCHEMA_V3,
        }
        or value["cycle_id"] != manifest["cycle_id"]
        or value["task_id"] != manifest["job4"]["task_id"]
        or value["status"] not in {"completed", "failed"}
    ):
        raise CycleError("Job 4 result identity or status is invalid")
    if schema_version in {JOB4_RESULT_SCHEMA_V2, JOB4_RESULT_SCHEMA_V3}:
        if (
            value["terminal_evidence_relative_path"]
            != "source/JOB4_TERMINAL_EVIDENCE.json"
        ):
            raise CycleError("Job 4 terminal-evidence path is invalid")
        require_hash(
            value["terminal_evidence_sha256"], "terminal_evidence_sha256"
        )
    if schema_version == JOB4_RESULT_SCHEMA_V3:
        try:
            diagnostics = ContinuousJob4TestDiagnosticsV1.from_dict(
                value["test_diagnostics"]
            )
        except ValueError as exc:
            raise CycleError("Job 4 test diagnostics are invalid") from exc
        if (
            require_hash(
                value["test_diagnostics_sha256"],
                "test_diagnostics_sha256",
            )
            != diagnostics.sha256
            or require_hash(
                value["test_records_root_sha256"],
                "test_records_root_sha256",
            )
            != diagnostics.records_root_sha256
            or value["test_record_count"] != len(diagnostics.records)
        ):
            raise CycleError("Job 4 test diagnostics identity changed")
    effects = value["effects"]
    if not isinstance(effects, dict):
        raise CycleError("Job 4 effects must be an object")
    exact_keys(
        effects,
        ("provider_calls", "story_database_writes", "active_route_changes", "deployment_remote_or_push_effects"),
        "Job 4 effects",
    )
    if not all(isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in effects.values()):
        raise CycleError("Job 4 effects must be non-negative integer declarations")
    verification = value["verification"]
    if not isinstance(verification, list) or not verification:
        raise CycleError("Job 4 verification must be a non-empty list")
    for item in verification:
        if not isinstance(item, dict):
            raise CycleError("Job 4 verification entry must be an object")
        exact_keys(item, ("command", "status", "summary"), "Job 4 verification entry")
        require_string(item["command"], "verification.command")
        if item["status"] not in {"passed", "failed", "environment_failed"}:
            raise CycleError("Job 4 verification status is invalid")
        require_string(item["summary"], "verification.summary")
    return value


def validate_job4_result(
    root: Path, cycle: Path, manifest: Mapping[str, Any], delay: int
) -> tuple[bytes, dict[str, Any], bytes, str]:
    relative = manifest["job4"]["expected_result_relative_path"]
    expected = resolve_inside(
        root,
        str(cycle / relative),
        "Job 4 result",
        must_exist=True,
        suffixes={".json"},
    )
    data, digest = stable_read(expected, delay)
    try:
        value = validate_job4_result_contract(
            json.loads(data.decode("utf-8-sig")), manifest
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CycleError("Job 4 result is invalid JSON") from exc
    report = resolve_inside(
        root,
        str(cycle / require_string(value["report_relative_path"], "report_relative_path")),
        "Job 4 report",
        must_exist=True,
        suffixes={".md"},
    )
    report_data, report_digest = stable_read(report, delay)
    if report_digest != require_hash(value["report_sha256"], "report_sha256"):
        raise CycleError("Job 4 report hash mismatch")
    report_data.decode("utf-8-sig")
    return data, value, report_data, digest


def validate_job4_terminal_evidence(
    root: Path,
    cycle: Path,
    result: Mapping[str, Any],
    delay: int,
) -> tuple[bytes, str] | None:
    if result["schema_version"] == JOB4_RESULT_SCHEMA:
        return None
    relative = require_string(
        result["terminal_evidence_relative_path"],
        "terminal_evidence_relative_path",
    )
    path = resolve_inside(
        root,
        str(cycle / relative),
        "Job 4 terminal evidence",
        must_exist=True,
        suffixes={".json"},
    )
    data, digest = stable_read(path, delay)
    expected = require_hash(
        result["terminal_evidence_sha256"], "terminal_evidence_sha256"
    )
    if digest != expected:
        raise CycleError("Job 4 terminal-evidence hash mismatch")
    try:
        raw = json.loads(data.decode("utf-8-sig"))
        terminal = decode_continuous_job4_terminal_evidence(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CycleError("Job 4 terminal evidence is invalid") from exc
    if terminal.sha256 != digest:
        raise CycleError("Job 4 terminal evidence is not canonical exact bytes")
    if (
        terminal.status != result["status"]
        or terminal.effect_evidence.canonical_effects != result["effects"]
    ):
        raise CycleError("Job 4 result contradicts typed terminal evidence")
    if result["schema_version"] == JOB4_RESULT_SCHEMA_V3:
        if not isinstance(terminal, ContinuousJob4TerminalEvidenceV6):
            raise CycleError("Job 4 v3 result lacks terminal test diagnostics")
        diagnostics = terminal.test_diagnostics
        if (
            result["test_diagnostics"] != diagnostics.to_dict()
            or result["test_diagnostics_sha256"] != diagnostics.sha256
            or result["test_records_root_sha256"]
            != diagnostics.records_root_sha256
            or result["test_record_count"] != len(diagnostics.records)
        ):
            raise CycleError(
                "Job 4 result contradicts terminal test diagnostics"
            )
    return data, digest


def complete_job4(
    cycle_directory: Path,
    *,
    stability_delay_milliseconds: int = 250,
    repository_root_path: Path | None = None,
) -> dict[str, Any]:
    root = repository_root(repository_root_path)
    cycle = cycle_path(root, cycle_directory)
    manifest = load_v2_manifest(root, cycle)
    _, started_hash = validate_publication(root, cycle, manifest)
    trigger_path = cycle / "receipts" / "TRIGGER_SENT.json"
    predecessor = started_hash
    state_receipt_name = "JOB4_STARTED.json"
    if trigger_path.exists():
        _, predecessor = validate_trigger_receipt(cycle, manifest, started_hash)
        state_receipt_name = "TRIGGER_SENT.json"
    validate_state_view(
        cycle,
        manifest,
        STATE_JOB4_IN_PROGRESS,
        state_receipt_name,
        predecessor,
    )
    data, result, report_data, result_hash = validate_job4_result(
        root, cycle, manifest, stability_delay_milliseconds
    )
    terminal_evidence = validate_job4_terminal_evidence(
        root, cycle, result, stability_delay_milliseconds
    )
    immutable_write(cycle / "artifacts" / "JOB4_RESULT.json", data)
    immutable_write(cycle / "artifacts" / "JOB4_REPORT.md", report_data)
    completion_event = "job4_completed"
    completion_fields: dict[str, Any] = {
        "job4_task_id": manifest["job4"]["task_id"],
        "job4_status": result["status"],
        "job4_result_sha256": result_hash,
        "job4_result_relative_path": "artifacts/JOB4_RESULT.json",
        "job4_report_sha256": result["report_sha256"],
        "job4_report_relative_path": "artifacts/JOB4_REPORT.md",
        "effect_claim_source": "structured_job4_result_declaration",
        "effects": result["effects"],
    }
    if terminal_evidence is not None:
        terminal_data, terminal_hash = terminal_evidence
        immutable_write(
            cycle / "artifacts" / "JOB4_TERMINAL_EVIDENCE.json",
            terminal_data,
        )
        completion_event = "job4_completed_v2"
        completion_fields.update(
            {
                "terminal_evidence_sha256": terminal_hash,
                "terminal_evidence_relative_path": (
                    "artifacts/JOB4_TERMINAL_EVIDENCE.json"
                ),
                "effect_claim_source": "typed_terminal_evidence_v2",
            }
        )
        if result["schema_version"] == JOB4_RESULT_SCHEMA_V3:
            completion_event = "job4_completed_v3"
            completion_fields.update(
                {
                    "test_diagnostics_sha256": result[
                        "test_diagnostics_sha256"
                    ],
                    "test_records_root_sha256": result[
                        "test_records_root_sha256"
                    ],
                    "test_record_count": result["test_record_count"],
                    "effect_claim_source": (
                        "typed_terminal_and_test_diagnostics_v3"
                    ),
                }
            )
    receipt = make_receipt(
        manifest,
        completion_event,
        predecessor,
        completion_fields,
    )
    path = cycle / "receipts" / "JOB4_COMPLETED.json"
    receipt_write(path, receipt)
    receipt_hash = sha256_bytes(path.read_bytes())
    state = state_value(manifest, STATE_RESPONSE_PENDING, "JOB4_COMPLETED.json", receipt_hash)
    atomic_replace(cycle / "state" / "CYCLE_STATE.json", canonical_json_bytes(state))
    return state


def parse_response(
    data: bytes,
    template_hash: str,
    *,
    require_planning_sections: bool = False,
) -> dict[str, str]:
    if sha256_bytes(data) == template_hash:
        raise CycleError("response is the unchanged placeholder template")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CycleError("response is not valid UTF-8") from exc
    folded = text.casefold()
    for placeholder in (
        "replace this placeholder",
        "replace this line",
        "todo: complete review",
        "pending review",
    ):
        if placeholder in folded:
            raise CycleError("response still contains a placeholder")
    if "## independent findings" not in folded:
        raise CycleError("response lacks the completed Independent findings section")
    findings = folded.split("## independent findings", 1)[1].strip()
    if len(findings) < 200:
        raise CycleError("response findings are not substantive")
    if require_planning_sections:
        for heading in (
            "## required corrections",
            "## next three progressions",
            "## recommended next job 4",
            "## explicitly not authorized",
        ):
            if heading not in folded:
                raise CycleError(f"response lacks required planning section: {heading}")
            body = folded.split(heading, 1)[1].split("\n## ", 1)[0].strip()
            if len(body) < 20:
                raise CycleError(f"response planning section is incomplete: {heading}")
    fields = (
        "review_cycle_id",
        "reviewed_checkpoint_id",
        "reviewed_checkpoint_git_sha",
        "reviewed_evidence_sha256",
        "reviewed_task_set_sha256",
        "reviewed_job4_task_id",
        "response_nonce",
        "review_scope",
        "review_disposition",
    )
    head = text[:12288]
    parsed: dict[str, str] = {}
    for field in fields:
        matches = re.findall(rf"(?m)^\s*{field}\s*:\s*([^\r\n]+?)\s*$", head)
        if len(matches) != 1 or not RESPONSE_VALUE.fullmatch(matches[0].strip()):
            raise CycleError(f"response must contain one valid {field}")
        parsed[field] = matches[0].strip()
    if parsed["review_scope"] != "repository_cycle":
        raise CycleError("response scope is invalid")
    if parsed["review_disposition"] not in {"accepted", "corrections_required", "blocked"}:
        raise CycleError("response disposition is not final")
    return parsed


def response_mismatches(manifest: Mapping[str, Any], parsed: Mapping[str, str]) -> list[str]:
    checkpoint = manifest["checkpoint"]
    expected = {
        "review_cycle_id": manifest["cycle_id"],
        "reviewed_checkpoint_id": checkpoint["id"],
        "reviewed_checkpoint_git_sha": checkpoint["git_sha"],
        "reviewed_evidence_sha256": checkpoint["evidence_sha256"],
        "reviewed_task_set_sha256": manifest["task_set_sha256"],
        "reviewed_job4_task_id": manifest["job4"]["task_id"],
        "response_nonce": manifest["response_nonce"],
    }
    return sorted(key for key, value in expected.items() if parsed.get(key) != value)


def rejection_receipt(
    cycle: Path,
    manifest: Mapping[str, Any],
    response_hash: str,
    failed_fields: Sequence[str],
    reason: str,
    state_receipt_hash: str,
) -> None:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", reason).strip("_")[:80]
    value = {
        "schema_version": RECEIPT_SCHEMA,
        "cycle_id": manifest["cycle_id"],
        "event": "response_rejected",
        "manifest_root_sha256": manifest["manifest_root_sha256"],
        "observed_state_receipt_sha256": state_receipt_hash,
        "response_sha256": response_hash,
        "failed_fields": sorted(set(failed_fields)),
        "reason_code": safe,
        "progression_state_changed": False,
        "response_text_retained": False,
        "recorded_at_utc": utc_now(),
    }
    receipt_write(
        cycle / "receipts" / "rejections" / f"RESPONSE_{response_hash[:16]}_{safe}.json",
        value,
    )


def completed_chain(
    root: Path,
    cycle: Path,
    manifest: Mapping[str, Any],
    *,
    require_current_source: bool = True,
) -> tuple[dict[str, Any], str]:
    _, started_hash = validate_publication(
        root, cycle, manifest, require_current_source=require_current_source
    )
    predecessor = started_hash
    trigger = cycle / "receipts" / "TRIGGER_SENT.json"
    if trigger.exists():
        _, predecessor = validate_trigger_receipt(cycle, manifest, started_hash)
    completion_path = cycle / "receipts" / "JOB4_COMPLETED.json"
    completion_event = read_json(completion_path).get("event")
    if completion_event not in {
        "job4_completed",
        "job4_completed_v2",
        "job4_completed_v3",
    }:
        raise CycleError("Job 4 completion receipt event is invalid")
    completion, completion_hash = validate_receipt(
        completion_path, manifest, completion_event, predecessor
    )
    if (
        completion["job4_task_id"] != manifest["job4"]["task_id"]
        or completion["job4_result_relative_path"] != "artifacts/JOB4_RESULT.json"
        or completion["job4_report_relative_path"] != "artifacts/JOB4_REPORT.md"
    ):
        raise CycleError("Job 4 completion receipt identity is invalid")
    result_path = resolve_inside(
        root,
        str(cycle / completion["job4_result_relative_path"]),
        "completed Job 4 result",
        must_exist=True,
        suffixes={".json"},
    )
    result_data = result_path.read_bytes()
    if sha256_bytes(result_data) != completion["job4_result_sha256"]:
        raise CycleError("Job 4 completion result does not match its receipt")
    try:
        result = validate_job4_result_contract(
            json.loads(result_data.decode("utf-8-sig")), manifest
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CycleError("completed Job 4 result is invalid JSON") from exc
    if result["schema_version"] in {
        JOB4_RESULT_SCHEMA_V2,
        JOB4_RESULT_SCHEMA_V3,
    }:
        expected_event = (
            "job4_completed_v3"
            if result["schema_version"] == JOB4_RESULT_SCHEMA_V3
            else "job4_completed_v2"
        )
        expected_claim_source = (
            "typed_terminal_and_test_diagnostics_v3"
            if result["schema_version"] == JOB4_RESULT_SCHEMA_V3
            else "typed_terminal_evidence_v2"
        )
        if (
            completion_event != expected_event
            or completion["effect_claim_source"] != expected_claim_source
            or completion["terminal_evidence_relative_path"]
            != "artifacts/JOB4_TERMINAL_EVIDENCE.json"
            or completion["terminal_evidence_sha256"]
            != result["terminal_evidence_sha256"]
        ):
            raise CycleError("Job 4 terminal-evidence receipt identity is invalid")
        terminal_path = resolve_inside(
            root,
            str(cycle / completion["terminal_evidence_relative_path"]),
            "completed Job 4 terminal evidence",
            must_exist=True,
            suffixes={".json"},
        )
        terminal_data = terminal_path.read_bytes()
        if sha256_bytes(terminal_data) != completion["terminal_evidence_sha256"]:
            raise CycleError("completed terminal evidence does not match its receipt")
        try:
            terminal = decode_continuous_job4_terminal_evidence(
                json.loads(terminal_data.decode("utf-8-sig"))
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise CycleError("completed Job 4 terminal evidence is invalid") from exc
        if (
            terminal.sha256 != completion["terminal_evidence_sha256"]
            or terminal.status != result["status"]
            or terminal.effect_evidence.canonical_effects != result["effects"]
        ):
            raise CycleError("completed terminal evidence contradicts Job 4 result")
        if result["schema_version"] == JOB4_RESULT_SCHEMA_V3:
            if not isinstance(terminal, ContinuousJob4TerminalEvidenceV6):
                raise CycleError(
                    "completed Job 4 v3 lacks terminal test diagnostics"
                )
            diagnostics = terminal.test_diagnostics
            if (
                completion["test_diagnostics_sha256"]
                != diagnostics.sha256
                or completion["test_records_root_sha256"]
                != diagnostics.records_root_sha256
                or completion["test_record_count"]
                != len(diagnostics.records)
                or result["test_diagnostics"] != diagnostics.to_dict()
                or result["test_diagnostics_sha256"]
                != diagnostics.sha256
                or result["test_records_root_sha256"]
                != diagnostics.records_root_sha256
                or result["test_record_count"] != len(diagnostics.records)
            ):
                raise CycleError(
                    "completed Job 4 test diagnostics changed"
                )
    elif (
        completion_event != "job4_completed"
        or completion["effect_claim_source"]
        != "structured_job4_result_declaration"
    ):
        raise CycleError("legacy Job 4 completion receipt identity is invalid")
    report_path = resolve_inside(
        root,
        str(cycle / completion["job4_report_relative_path"]),
        "completed Job 4 report",
        must_exist=True,
        suffixes={".md"},
    )
    report_data = report_path.read_bytes()
    try:
        report_data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CycleError("completed Job 4 report is not UTF-8") from exc
    if (
        sha256_bytes(report_data) != completion["job4_report_sha256"]
        or result["report_sha256"] != completion["job4_report_sha256"]
        or result["status"] != completion["job4_status"]
        or result["effects"] != completion["effects"]
    ):
        raise CycleError("Job 4 completion artifacts do not match the receipt")
    return completion, completion_hash


def validate_consumed_cycle(
    root: Path,
    cycle: Path,
    manifest: Mapping[str, Any],
    *,
    require_current_source: bool,
) -> tuple[dict[str, Any], str, str, bytes]:
    completion, completion_hash = completed_chain(
        root,
        cycle,
        manifest,
        require_current_source=require_current_source,
    )
    consumed, consumed_hash = validate_receipt(
        cycle / "receipts" / "RESPONSE_CONSUMED.json",
        manifest,
        "response_consumed",
        completion_hash,
    )
    if (
        consumed["response_relative_path"]
        != ACCEPTED_RESPONSE_RELATIVE_PATH.as_posix()
        or consumed["review_disposition"]
        not in {"accepted", "corrections_required", "blocked"}
        or consumed["identity_validation"] != "passed"
        or consumed["completeness_validation"] != "passed"
        or consumed["stable_read_validation"] != "passed"
        or consumed["advisory_only"] is not True
        or consumed["creator_authority_granted"] is not False
    ):
        raise CycleError("response consumption receipt claims are invalid")
    accepted = cycle / ACCEPTED_RESPONSE_RELATIVE_PATH
    if not accepted.is_file():
        raise CycleError("consumed cycle is missing its accepted response")
    data = accepted.read_bytes()
    if sha256_bytes(data) != consumed["response_sha256"]:
        raise CycleError("accepted response bytes do not match the consumption receipt")
    parsed = parse_response(
        data,
        sha256_bytes((cycle / "outbox" / "PRO_RESPONSE_TEMPLATE.md").read_bytes()),
        require_planning_sections=manifest["cycle_sequence"] >= 7,
    )
    if response_mismatches(manifest, parsed):
        raise CycleError("consumed response identity does not match the manifest")
    if parsed["review_disposition"] != consumed["review_disposition"]:
        raise CycleError("consumed response disposition does not match the receipt")
    return completion, completion_hash, consumed_hash, data


def post_consumption_inbox_diagnostic(
    cycle: Path, accepted_data: bytes, *, accepted_authority_validated: bool
) -> dict[str, Any]:
    """Describe staging-inbox residue without granting it response authority."""

    inbox = cycle / EXPECTED_RESPONSE_RELATIVE_PATH
    accepted_hash = sha256_bytes(accepted_data)
    if not inbox.is_file():
        state = "absent"
        inbox_hash: str | None = None
    else:
        inbox_data = inbox.read_bytes()
        inbox_hash = sha256_bytes(inbox_data)
        state = "identical" if inbox_data == accepted_data else "conflicting"
    return {
        "accepted_response_authority": (
            "consumption_receipt_bound_accepted_copy"
            if accepted_authority_validated
            else "unvalidated_status_view"
        ),
        "accepted_response_sha256": accepted_hash,
        "post_consumption_inbox_state": state,
        "post_consumption_inbox_sha256": inbox_hash,
        "post_consumption_inbox_conflict": state == "conflicting",
        "post_consumption_inbox_authoritative": False,
        "post_consumption_inbox_diagnostic_authoritative": False,
    }


def recover_state_view(
    cycle: Path,
    manifest: Mapping[str, Any],
    state_name: str,
    receipt_name: str,
    receipt_hash: str,
) -> dict[str, Any]:
    """Return an already-valid state view unchanged or rebuild only that view."""

    state_path = cycle / "state" / "CYCLE_STATE.json"
    if state_path.is_file():
        try:
            return validate_state_view(
                cycle, manifest, state_name, receipt_name, receipt_hash
            )
        except (CycleError, OSError):
            pass
    state = state_value(manifest, state_name, receipt_name, receipt_hash)
    atomic_replace(state_path, canonical_json_bytes(state))
    return state


def consume_response(
    cycle_directory: Path,
    *,
    stability_delay_milliseconds: int = 250,
    repository_root_path: Path | None = None,
) -> dict[str, Any]:
    root = repository_root(repository_root_path)
    cycle = cycle_path(root, cycle_directory)
    manifest = load_v2_manifest(root, cycle)
    _, completion_hash = completed_chain(root, cycle, manifest)
    response_path = cycle / EXPECTED_RESPONSE_RELATIVE_PATH
    data, response_hash = stable_read(response_path, stability_delay_milliseconds)
    accepted = cycle / ACCEPTED_RESPONSE_RELATIVE_PATH
    if accepted.exists() and accepted.read_bytes() != data:
        rejection_receipt(
            cycle, manifest, response_hash, ("response_sha256",),
            "conflicting_response_after_acceptance", completion_hash
        )
        raise CycleError("response conflicts with the already accepted review")
    template_hash = sha256_bytes((cycle / "outbox" / "PRO_RESPONSE_TEMPLATE.md").read_bytes())
    try:
        parsed = parse_response(
            data,
            template_hash,
            require_planning_sections=manifest["cycle_sequence"] >= 7,
        )
    except CycleError:
        rejection_receipt(
            cycle, manifest, response_hash, ("response_format",),
            "invalid_or_incomplete_response", completion_hash
        )
        raise
    mismatches = response_mismatches(manifest, parsed)
    if mismatches:
        rejection_receipt(
            cycle, manifest, response_hash, mismatches, "identity_mismatch", completion_hash
        )
        raise CycleError("response identity does not match: " + ", ".join(mismatches))
    immutable_write(accepted, data)
    receipt = make_receipt(
        manifest,
        "response_consumed",
        completion_hash,
        {
            "response_sha256": response_hash,
            "response_relative_path": ACCEPTED_RESPONSE_RELATIVE_PATH.as_posix(),
            "review_disposition": parsed["review_disposition"],
            "identity_validation": "passed",
            "completeness_validation": "passed",
            "stable_read_validation": "passed",
            "advisory_only": True,
            "creator_authority_granted": False,
        },
    )
    path = cycle / "receipts" / "RESPONSE_CONSUMED.json"
    receipt_write(path, receipt)
    receipt_hash = sha256_bytes(path.read_bytes())
    state = state_value(manifest, STATE_REVIEW_CONSUMED, "RESPONSE_CONSUMED.json", receipt_hash)
    atomic_replace(cycle / "state" / "CYCLE_STATE.json", canonical_json_bytes(state))
    return state


def wait_and_consume(
    cycle_directory: Path,
    *,
    max_polls: int,
    poll_seconds: float,
    stability_delay_milliseconds: int,
    repository_root_path: Path | None = None,
) -> dict[str, Any]:
    if not 1 <= max_polls <= 120 or not 0 <= poll_seconds <= 300:
        raise CycleError("bounded wait arguments are outside the contract")
    for attempt in range(1, max_polls + 1):
        try:
            return consume_response(
                cycle_directory,
                stability_delay_milliseconds=stability_delay_milliseconds,
                repository_root_path=repository_root_path,
            )
        except ResponseNotReady:
            if attempt < max_polls:
                time.sleep(poll_seconds)
    root = repository_root(repository_root_path)
    cycle = cycle_path(root, cycle_directory)
    manifest = load_v2_manifest(root, cycle)
    waits = cycle / "receipts" / "waits"
    sequence = len(list(waits.glob("WAIT_EXHAUSTED_*.json"))) + 1 if waits.exists() else 1
    value = {
        "schema_version": RECEIPT_SCHEMA,
        "cycle_id": manifest["cycle_id"],
        "event": "bounded_wait_exhausted",
        "manifest_root_sha256": manifest["manifest_root_sha256"],
        "wait_sequence": sequence,
        "max_polls": max_polls,
        "poll_seconds": poll_seconds,
        "exact_response_relative_path": manifest["expected_response_relative_path"],
        "progression_state_changed": False,
        "unrelated_work_started": False,
        "recorded_at_utc": utc_now(),
    }
    receipt_write(waits / f"WAIT_EXHAUSTED_{sequence:04d}.json", value)
    raise ResponseNotReady("bounded repository response wait expired")


def recover_cycle(
    cycle_directory: Path, *, repository_root_path: Path | None = None
) -> dict[str, Any]:
    root = repository_root(repository_root_path)
    cycle = cycle_path(root, cycle_directory)
    manifest = load_v2_manifest(root, cycle)
    consumed_path = cycle / "receipts" / "RESPONSE_CONSUMED.json"
    if consumed_path.exists():
        _, _, consumed_hash, _ = validate_consumed_cycle(
            root, cycle, manifest, require_current_source=False
        )
        return recover_state_view(
            cycle,
            manifest,
            STATE_REVIEW_CONSUMED,
            "RESPONSE_CONSUMED.json",
            consumed_hash,
        )

    _, started_hash = validate_publication(root, cycle, manifest)
    predecessor = started_hash
    last_name = "JOB4_STARTED.json"
    state_name = STATE_JOB4_IN_PROGRESS
    trigger = cycle / "receipts" / "TRIGGER_SENT.json"
    if trigger.exists():
        _, predecessor = validate_trigger_receipt(cycle, manifest, started_hash)
        last_name = "TRIGGER_SENT.json"
    completion_path = cycle / "receipts" / "JOB4_COMPLETED.json"
    if completion_path.exists():
        _, predecessor = completed_chain(root, cycle, manifest)
        state_name = STATE_RESPONSE_PENDING
        last_name = "JOB4_COMPLETED.json"
    if state_name == STATE_RESPONSE_PENDING:
        accepted = cycle / ACCEPTED_RESPONSE_RELATIVE_PATH
        inbox = cycle / EXPECTED_RESPONSE_RELATIVE_PATH
        if accepted.exists() and inbox.exists() and accepted.read_bytes() == inbox.read_bytes():
            recover_state_view(cycle, manifest, state_name, last_name, predecessor)
            return consume_response(
                cycle, stability_delay_milliseconds=0, repository_root_path=root
            )
    return recover_state_view(cycle, manifest, state_name, last_name, predecessor)


def cycle_status(
    cycle_directory: Path, *, repository_root_path: Path | None = None
) -> dict[str, Any]:
    root = repository_root(repository_root_path)
    cycle = cycle_path(root, cycle_directory)
    manifest = load_manifest_file(cycle / "CYCLE_MANIFEST.json")
    state = read_json(cycle / "state" / "CYCLE_STATE.json")
    result = {
        "cycle_id": manifest["cycle_id"],
        "cycle_sequence": manifest["cycle_sequence"],
        "state": state["state"],
        "checkpoint_id": manifest["checkpoint"]["id"],
        "checkpoint_git_sha": manifest["checkpoint"]["git_sha"],
        "task_set_sha256": manifest["task_set_sha256"],
        "job4_task_id": manifest["job4"]["task_id"],
        "response_detected": (cycle / EXPECTED_RESPONSE_RELATIVE_PATH).is_file(),
        "response_consumed": (cycle / ACCEPTED_RESPONSE_RELATIVE_PATH).is_file(),
        "review_is_advisory": True,
        "creator_authority_granted": False,
        "state_view_validation": "not_performed_status_only",
    }
    accepted = cycle / ACCEPTED_RESPONSE_RELATIVE_PATH
    if accepted.is_file():
        result.update(
            post_consumption_inbox_diagnostic(
                cycle,
                accepted.read_bytes(),
                accepted_authority_validated=False,
            )
        )
    return result


def latest_consumed_cycle(*, repository_root_path: Path | None = None) -> dict[str, Any]:
    root = repository_root(repository_root_path)
    cycles_root = root / ".chatgpt" / "pro-review" / "cycles"
    candidates: list[tuple[int, Path, dict[str, Any]]] = []
    invalid: list[str] = []
    if cycles_root.exists():
        for path in cycles_root.iterdir():
            manifest_path = path / "CYCLE_MANIFEST.json"
            consumed_path = path / "receipts" / "RESPONSE_CONSUMED.json"
            if not manifest_path.is_file() or not consumed_path.is_file():
                continue
            try:
                manifest = load_manifest_file(manifest_path)
                sequence = manifest["cycle_sequence"]
                if not isinstance(sequence, int) or isinstance(sequence, bool):
                    raise CycleError("cycle sequence is invalid")
                candidates.append((sequence, path, manifest))
            except (CycleError, KeyError, ValueError, TypeError):
                invalid.append(path.name)
    if not candidates:
        raise CycleError("no consumed repository review cycle exists")
    for _, path, manifest in sorted(
        candidates, key=lambda item: (item[0], item[1].name), reverse=True
    ):
        try:
            if manifest.get("schema_version") in {
                MANIFEST_SCHEMA,
                MANIFEST_SCHEMA_V3,
            }:
                _, _, consumed_hash, data = validate_consumed_cycle(
                    root, path, manifest, require_current_source=False
                )
                validate_state_view(
                    path,
                    manifest,
                    STATE_REVIEW_CONSUMED,
                    "RESPONSE_CONSUMED.json",
                    consumed_hash,
                )
            else:
                consumed = read_json(path / "receipts" / "RESPONSE_CONSUMED.json")
                accepted = path / ACCEPTED_RESPONSE_RELATIVE_PATH
                if (
                    consumed.get("cycle_id") != manifest.get("cycle_id")
                    or consumed.get("event") != "response_consumed"
                    or not accepted.is_file()
                    or sha256_bytes(accepted.read_bytes())
                    != consumed.get("response_sha256")
                ):
                    raise CycleError("legacy consumed response evidence is invalid")
                data = accepted.read_bytes()
            return {
                "cycle_id": manifest["cycle_id"],
                "cycle_sequence": manifest["cycle_sequence"],
                "accepted_response_path": str(path / ACCEPTED_RESPONSE_RELATIVE_PATH),
                "review_is_advisory": True,
                "skipped_invalid_cycles": sorted(set(invalid)),
                **post_consumption_inbox_diagnostic(
                    path, data, accepted_authority_validated=True
                ),
            }
        except (CycleError, KeyError, ValueError, TypeError, OSError):
            invalid.append(path.name)
    raise CycleError(
        "no valid consumed repository review cycle exists; rejected="
        + ",".join(sorted(set(invalid)))
    )
