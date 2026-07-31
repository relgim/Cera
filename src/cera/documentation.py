"""Repository-local documentation integrity validation."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys

from cera.active_runtime import ACTIVE_RUNTIME_PROFILE


REQUIRED_DOCUMENTS = (
    "README.md",
    "AGENTS.md",
    "docs/START_HERE.md",
    "docs/authority/CODEX_PROGRESS_REVIEW_PROTOCOL.md",
    "docs/authority/CERA_OWNER_ARCHITECTURE.md",
    "docs/authority/CREATOR_FACTS_AND_PREFERENCES.md",
    "docs/authority/DECISIONS_AND_SUPERSESSIONS.md",
    "docs/authority/SOURCE_PROVENANCE.md",
    "docs/architecture/RUNTIME_PIPELINE_AND_PORTS.md",
    "docs/architecture/BEHAVIORAL_AUTHORITY_AND_SCENE_DEVELOPMENT.md",
    "docs/architecture/GENESIS_MEMORY_AND_RETRIEVAL.md",
    "docs/architecture/PROMPT_CONTEXT_AND_EXAMPLES.md",
    "docs/architecture/BLOCKED_TURN_AND_RESUMPTION.md",
    "docs/contracts/SCHEMA_CATALOG.md",
    "docs/contracts/STATE_MACHINES_AND_ERRORS.md",
    "docs/workflows/END_TO_END_WORKFLOWS.md",
    "docs/implementation/ROADMAP_AND_GATE.md",
    "docs/implementation/PHASE_8_RESULT.md",
    "docs/implementation/PHASE_9_RESULT.md",
    "docs/handoff/CURRENT.md",
    "docs/handoff/PRO_WRITING_REVIEW_PACKAGE.md",
    ".chatgpt/pro-review/README.md",
    ".chatgpt/pro-review/REQUEST_TEMPLATE.md",
)
REFERENCE_RUNTIME_MARKERS = (
    "D:\\AIChatBot\\Vera_v2_d3",
    "E:\\AIChatBot\\RPProxy_DeepSeek_Direct",
    "E:\\AIChatBot\\Sera",
)
MOJIBAKE_MARKERS = ("\ufffd", "â€", "Ã", "Â")
LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
EXCLUDED_DOCUMENTATION_TREES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}
CURRENT_RUNTIME_DOCUMENTS = (
    "README.md",
    "docs/START_HERE.md",
    "docs/handoff/CURRENT.md",
    "docs/implementation/ROADMAP_AND_GATE.md",
)
CURRENT_RUNTIME_BEGIN = "<!-- CERA_CURRENT_RUNTIME_BEGIN -->"
CURRENT_RUNTIME_END = "<!-- CERA_CURRENT_RUNTIME_END -->"


@dataclass(frozen=True, slots=True)
class DocumentationFinding:
    code: str
    path: str
    detail: str


def validate_documentation(project_root: Path) -> tuple[DocumentationFinding, ...]:
    root = project_root.resolve()
    findings: list[DocumentationFinding] = []
    for relative in REQUIRED_DOCUMENTS:
        path = root / relative
        if not path.is_file():
            findings.append(DocumentationFinding("DOC_MISSING", relative, "required file missing"))
    for path in sorted(root.rglob("*.md")):
        if any(part in EXCLUDED_DOCUMENTATION_TREES for part in path.relative_to(root).parts):
            continue
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        for marker in MOJIBAKE_MARKERS:
            if marker in text:
                findings.append(
                    DocumentationFinding("DOC_ENCODING", relative, f"contains marker {marker!r}")
                )
        for match in LINK_PATTERN.finditer(text):
            target = match.group(1).split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "#", "<")):
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                findings.append(DocumentationFinding("DOC_LINK", relative, target))
    for path in sorted((root / "src").rglob("*.py")) if (root / "src").exists() else ():
        if path.name == "documentation.py":
            continue
        text = path.read_text(encoding="utf-8")
        normalized_text = text.replace("\\\\", "\\").replace("/", "\\")
        for marker in REFERENCE_RUNTIME_MARKERS:
            if marker in normalized_text:
                findings.append(
                    DocumentationFinding(
                        "REFERENCE_RUNTIME_DEPENDENCY",
                        path.relative_to(root).as_posix(),
                        marker,
                    )
                )
    findings.extend(validate_current_runtime_documents(root))
    return tuple(findings)


def validate_current_runtime_documents(
    project_root: Path,
) -> tuple[DocumentationFinding, ...]:
    """Reject stale active-runtime claims without scanning historical evidence."""

    root = project_root.resolve()
    profile = ACTIVE_RUNTIME_PROFILE
    required_tokens = (
        profile.decision_id,
        profile.profile_id,
        profile.profile_sha256,
        profile.reasoner_session_mode,
        profile.reasoner.domain_adapter_version,
        profile.reasoner.packet_version,
        profile.reasoner.prompt_version,
        profile.reasoner.tool_contract_version or "",
        profile.composer.model,
        profile.composer.domain_adapter_version,
        profile.composer.packet_version,
        profile.composer.prompt_version,
        "non-thinking" if profile.composer.thinking_enabled is False else "thinking",
        profile.verifier.domain_adapter_version,
        profile.verifier.prompt_version,
        profile.verifier.request_schema_version or "",
    )
    stale_tokens = (
        "Reasoner v24",
        "MCP v6",
        "Flash thinking",
        "Flash-thinking",
    )
    findings: list[DocumentationFinding] = []
    for relative in CURRENT_RUNTIME_DOCUMENTS:
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if (
            text.count(CURRENT_RUNTIME_BEGIN) != 1
            or text.count(CURRENT_RUNTIME_END) != 1
        ):
            findings.append(
                DocumentationFinding(
                    "CURRENT_RUNTIME_BLOCK",
                    relative,
                    "requires exactly one current-runtime marker block",
                )
            )
            continue
        block = text.split(CURRENT_RUNTIME_BEGIN, 1)[1].split(
            CURRENT_RUNTIME_END,
            1,
        )[0]
        for token in required_tokens:
            if token and token not in block:
                findings.append(
                    DocumentationFinding(
                        "CURRENT_RUNTIME_TOKEN",
                        relative,
                        f"missing {token}",
                    )
                )
        for token in stale_tokens:
            if token in block:
                findings.append(
                    DocumentationFinding(
                        "CURRENT_RUNTIME_STALE",
                        relative,
                        token,
                    )
                )
    return tuple(findings)


def main(argv: list[str] | None = None) -> int:
    arguments = argv if argv is not None else sys.argv[1:]
    root = Path(arguments[0]) if arguments else Path.cwd()
    findings = validate_documentation(root)
    print(
        json.dumps(
            {
                "schema_version": "cera.documentation_validation.v1",
                "project_root": str(root.resolve()),
                "ok": not findings,
                "findings": [
                    {"code": finding.code, "path": finding.path, "detail": finding.detail}
                    for finding in findings
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
