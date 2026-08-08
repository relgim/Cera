"""Human-readable, non-authoritative Pi Scene diagnostics.

Raw provider evidence and accepted Python state remain authoritative.  These
Markdown views exist only so Ted can inspect one chronological directory
without decoding JSONL, hashes, or provider-specific event envelopes.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4

from cera.errors import ContractValidationError
from cera.serialization import to_primitive


class ReadablePiSceneDebugLog:
    """Write atomic Markdown diagnostics into one flat directory."""

    def __init__(
        self,
        root: Path,
        *,
        enabled: bool = True,
        max_entries: int = 200,
    ) -> None:
        if type(enabled) is not bool:
            raise ContractValidationError("readable debug enabled flag is invalid")
        if type(max_entries) is not int or not 1 <= max_entries <= 10_000:
            raise ContractValidationError("readable debug retention bound is invalid")
        self.root = root.resolve()
        self.enabled = enabled
        self.max_entries = max_entries
        self._lock = RLock()
        self._sequence = 0
        if self.enabled:
            self.root.mkdir(parents=True, exist_ok=True)
            self._write_readme()

    def write(
        self,
        *,
        stage: str,
        identity: str,
        sections: Mapping[str, Any],
    ) -> Path | None:
        if not stage.strip() or not identity.strip() or not sections:
            raise ContractValidationError("readable debug entry is incomplete")
        if not self.enabled:
            return None
        with self._lock:
            self._sequence += 1
            timestamp = datetime.now(timezone.utc)
            stem = "_".join(
                (
                    timestamp.strftime("%Y-%m-%dT%H-%M-%S-%fZ"),
                    f"{self._sequence:04d}",
                    _safe_name(stage),
                    _safe_name(identity),
                )
            )
            target = self.root / f"{stem}.md"
            lines = [
                f"# CERA Debug - {stage}",
                "",
                f"- Time (UTC): `{timestamp.isoformat()}`",
                f"- Identity: `{identity}`",
                "- Authority: `diagnostic view only; raw evidence and accepted Python state control`",
                "",
            ]
            for heading, value in sections.items():
                lines.extend(_markdown_section(str(heading), value))
            content = "\n".join(lines).rstrip() + "\n"
            _atomic_text(target, content)
            _atomic_text(self.root / "LATEST.md", content)
            self._prune_and_rebuild_index()
            return target

    def writer_view(self, root: Path) -> Mapping[str, str]:
        if not self.enabled:
            return {}
        view_root = root.resolve()
        if not view_root.is_dir():
            raise ContractValidationError("readable debug Writer view is unavailable")
        files: dict[str, str] = {}
        total = 0
        for path in sorted(value for value in view_root.rglob("*") if value.is_file()):
            if path.is_symlink() or not path.resolve().is_relative_to(view_root):
                raise ContractValidationError("readable debug Writer view escaped its root")
            data = path.read_bytes()
            total += len(data)
            if len(data) > 1_000_000 or total > 4_000_000:
                raise ContractValidationError("readable debug Writer view exceeds its bound")
            files[path.relative_to(view_root).as_posix()] = data.decode("utf-8")
        return files

    def _prune_and_rebuild_index(self) -> None:
        entries = sorted(
            path
            for path in self.root.glob("*.md")
            if path.name not in {"README.md", "INDEX.md", "LATEST.md"}
        )
        for path in entries[:-self.max_entries]:
            path.unlink()
        retained = entries[-self.max_entries :]
        lines = ["# CERA Debug Index", ""]
        lines.extend(f"- [{path.name}]({path.name})" for path in retained)
        _atomic_text(self.root / "INDEX.md", "\n".join(lines).rstrip() + "\n")

    def _write_readme(self) -> None:
        readme = self.root / "README.md"
        if readme.exists():
            return
        _atomic_text(
            readme,
            "# CERA Human-Readable Debug Logs\n\n"
            "Open `LATEST.md` for the newest operation or `INDEX.md` for the chronological list. "
            "Planner, Writer, creator-decision, and Recorder inputs and outputs are labeled in plain Markdown. "
            "These files are diagnostic views, not story authority. Values under credential-like keys are redacted. "
            f"This directory retains at most {self.max_entries} timestamped entries and can be disabled with the runtime setting.\n",
        )
        if not (self.root / "INDEX.md").exists():
            _atomic_text(self.root / "INDEX.md", "# CERA Debug Index\n")


def _markdown_section(heading: str, value: Any) -> list[str]:
    primitive = _redact_secrets(to_primitive(value))
    if isinstance(primitive, str):
        body = primitive
        language = "text"
    else:
        body = json.dumps(primitive, ensure_ascii=False, indent=2, sort_keys=True)
        language = "json"
    return [f"## {heading}", "", f"```{language}", body, "```", ""]


_SENSITIVE_KEY = re.compile(
    r"(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|credential)",
    re.IGNORECASE,
)


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if _SENSITIVE_KEY.search(str(key))
                else _redact_secrets(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


def _safe_name(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    if not result:
        raise ContractValidationError("readable debug identity is invalid")
    return result[:96]


def _atomic_text(path: Path, content: str) -> None:
    stage = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        stage.write_text(content, encoding="utf-8", newline="\n")
        os.replace(stage, path)
    finally:
        if stage.exists():
            stage.unlink()
