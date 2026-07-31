from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from cera.documentation import validate_documentation


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def test_authoritative_documentation_is_complete_and_linked(self) -> None:
        self.assertEqual(validate_documentation(PROJECT_ROOT), ())

    def test_validator_detects_reference_runtime_dependency_in_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                "README.md",
                "AGENTS.md",
                "docs/START_HERE.md",
                "docs/authority/CERA_OWNER_ARCHITECTURE.md",
                "docs/authority/CREATOR_FACTS_AND_PREFERENCES.md",
                "docs/authority/DECISIONS_AND_SUPERSESSIONS.md",
                "docs/authority/SOURCE_PROVENANCE.md",
                "docs/architecture/RUNTIME_PIPELINE_AND_PORTS.md",
                "docs/architecture/GENESIS_MEMORY_AND_RETRIEVAL.md",
                "docs/architecture/PROMPT_CONTEXT_AND_EXAMPLES.md",
                "docs/architecture/BLOCKED_TURN_AND_RESUMPTION.md",
                "docs/contracts/SCHEMA_CATALOG.md",
                "docs/contracts/STATE_MACHINES_AND_ERRORS.md",
                "docs/workflows/END_TO_END_WORKFLOWS.md",
                "docs/implementation/ROADMAP_AND_GATE.md",
                "docs/implementation/PHASE_8_RESULT.md",
                "docs/handoff/CURRENT.md",
                "docs/handoff/PRO_WRITING_REVIEW_PACKAGE.md",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# test\n", encoding="utf-8")
            source = root / "src" / "bad.py"
            source.parent.mkdir(parents=True)
            source.write_text('ROOT = r"E:\\AIChatBot\\Sera"\n', encoding="utf-8")
            dependency_notice = root / ".venv" / "dependency" / "NOTICE.md"
            dependency_notice.parent.mkdir(parents=True)
            dependency_notice.write_text("[external](../../missing.txt)\n", encoding="utf-8")
            codes = {finding.code for finding in validate_documentation(root)}
            self.assertIn("REFERENCE_RUNTIME_DEPENDENCY", codes)
            self.assertNotIn("DOC_LINK", codes)


if __name__ == "__main__":
    unittest.main()
