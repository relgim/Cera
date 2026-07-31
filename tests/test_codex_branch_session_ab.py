from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_codex_branch_session_ab.py"


def load_script():
    spec = importlib.util.spec_from_file_location("cera_session_ab", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CodexBranchSessionABTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_script()

    def test_exact_requested_messages_and_long_scope(self) -> None:
        self.assertEqual(
            self.module.MESSAGES,
            (
                "Hello, my name is Ted. Is this the hanezawa household?",
                "Respond accordingly to Sakuras Response.",
                "Respond accordingly to Sakuras Response.",
            ),
        )

    def test_policies_compare_three_fresh_threads_with_one_continued_thread(self) -> None:
        self.assertEqual(
            [(value.key, value.reuse_thread) for value in self.module.POLICIES],
            [("fresh_each_turn", False), ("continued_thread", True)],
        )

    def test_provisional_history_is_non_authoritative(self) -> None:
        instruction = self.module.PROVISIONAL_HISTORY_INSTRUCTION
        self.assertIn("non-authoritative provisional context", instruction)
        self.assertIn("newest CERA packet is the complete current authority", instruction)
        self.assertIn("Never cite a prior draft as evidence", instruction)

    def test_turns_are_independently_addressable_without_changing_text(self) -> None:
        self.assertEqual(len(self.module.MESSAGES), 3)
        self.assertEqual(self.module.MESSAGES[1], self.module.MESSAGES[2])


if __name__ == "__main__":
    unittest.main()
