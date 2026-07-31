"""Pinned contract identity for one-shot ``codex exec`` structured output."""

from __future__ import annotations

from hashlib import sha256


CODEX_CLI_EXEC_COMPATIBILITY_ID = (
    "cera.codex_cli_exec_structured_output.v1"
)
CODEX_CLI_EXEC_TRANSPORT_NAME = "codex_cli_exec"
CODEX_CLI_EXEC_VERSION = "0.144.4"
_CONTRACT_TEXT = (
    "codex-cli-0.144.4|exec|ephemeral|ignore-user-config|ignore-rules|"
    "skip-git-repo-check|jsonl|output-schema|output-last-message|"
    "read-only|approval-never|web-search-disabled|mcp-empty|stdin-prompt|"
    "one-shot|no-retry|no-fallback|no-tool-events"
)
CODEX_CLI_EXEC_CONTRACT_SHA256 = sha256(
    _CONTRACT_TEXT.encode("utf-8")
).hexdigest()
