from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from cera.documentation import (
    CURRENT_RUNTIME_BEGIN,
    CURRENT_RUNTIME_DOCUMENTS,
    CURRENT_RUNTIME_END,
    REQUIRED_DOCUMENTS,
    validate_current_runtime_documents,
    validate_documentation,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def test_authoritative_documentation_is_complete_and_linked(self) -> None:
        self.assertEqual(validate_documentation(PROJECT_ROOT), ())

    def test_validator_detects_reference_runtime_dependency_in_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in REQUIRED_DOCUMENTS:
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

    def test_current_runtime_validator_rejects_stale_marked_claims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in CURRENT_RUNTIME_DOCUMENTS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    f"{CURRENT_RUNTIME_BEGIN}\nReasoner v24 with MCP v6\n"
                    f"{CURRENT_RUNTIME_END}\n",
                    encoding="utf-8",
                )
            findings = validate_current_runtime_documents(root)
            codes = {finding.code for finding in findings}
            self.assertIn("CURRENT_RUNTIME_STALE", codes)
            self.assertIn("CURRENT_RUNTIME_TOKEN", codes)


if __name__ == "__main__":
    unittest.main()
