from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cera.continuous.path_custody import exclusive_no_follow_file_lock
from cera.errors import StateConflictError


class NoFollowFileLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "trusted"
        self.root.mkdir()

    def test_lock_is_exclusive_and_released_by_context(self) -> None:
        with exclusive_no_follow_file_lock(self.root, "CLAIMS/owner.lock"):
            with self.assertRaisesRegex(StateConflictError, "already held"):
                with exclusive_no_follow_file_lock(self.root, "CLAIMS/owner.lock"):
                    self.fail("contended no-follow lock was acquired")
        with exclusive_no_follow_file_lock(self.root, "CLAIMS/owner.lock"):
            pass

    def test_os_releases_no_follow_lock_when_owner_process_crashes(self) -> None:
        source_root = Path(__file__).resolve().parents[1] / "src"
        script = "\n".join(
            (
                "import os",
                "from pathlib import Path",
                "from cera.continuous.path_custody import exclusive_no_follow_file_lock",
                f"root = Path({str(self.root)!r})",
                "with exclusive_no_follow_file_lock(root, 'CLAIMS/crash.lock'):",
                "    os._exit(23)",
            )
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(source_root)
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        self.assertEqual(result.returncode, 23, result.stderr)
        with exclusive_no_follow_file_lock(self.root, "CLAIMS/crash.lock"):
            pass

    def test_hard_link_alias_of_lock_file_fails_closed(self) -> None:
        relative = "CLAIMS/alias.lock"
        with exclusive_no_follow_file_lock(self.root, relative):
            pass
        original = self.root / "CLAIMS" / "alias.lock"
        alias = self.root / "lock-alias-outside-claims"
        os.link(original, alias)
        with self.assertRaisesRegex(StateConflictError, "hard-link|aliased"):
            with exclusive_no_follow_file_lock(self.root, relative):
                self.fail("hard-linked no-follow lock was acquired")


if __name__ == "__main__":
    unittest.main()
