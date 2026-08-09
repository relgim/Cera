from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cera.pi_scene.readable_debug import ReadablePiSceneDebugLog


class ReadablePiSceneDebugPrivacyTests(unittest.TestCase):
    def test_protected_adult_entry_is_not_written_to_ordinary_index_or_latest(self) -> None:
        with TemporaryDirectory() as temporary:
            log = ReadablePiSceneDebugLog(Path(temporary) / "debug")
            ordinary = log.write(
                stage="codex-cognition",
                identity="turn-ordinary",
                sections={"Input": "ordinary marker"},
            )
            protected = log.write(
                stage="deepseek-adult-scene",
                identity="turn-adult",
                protected=True,
                sections={"Exact output": "protected marker"},
            )

            assert ordinary is not None and protected is not None
            self.assertEqual(protected.parent.name, log.PROTECTED_DIRECTORY)
            ordinary_latest = (log.root / "LATEST.md").read_text(encoding="utf-8")
            ordinary_index = (log.root / "INDEX.md").read_text(encoding="utf-8")
            protected_latest = (protected.parent / "LATEST.md").read_text(encoding="utf-8")
            self.assertIn("ordinary marker", ordinary_latest)
            self.assertNotIn("protected marker", ordinary_latest)
            self.assertNotIn(protected.name, ordinary_index)
            self.assertIn("protected marker", protected_latest)
            self.assertIn(
                "must not be exposed to Codex",
                (protected.parent / "README.md").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
