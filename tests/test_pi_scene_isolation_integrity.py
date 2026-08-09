from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from cera.errors import StateConflictError
from cera.pi_scene.sillytavern_isolation import (
    stage_isolated_sillytavern,
    verify_isolated_sillytavern,
)


def _source(root: Path) -> Path:
    source = root / "source"
    for relative in ("default", "node_modules", "public", "src"):
        (source / relative).mkdir(parents=True, exist_ok=True)
    (source / "server.js").write_text("server-v1", encoding="utf-8")
    (source / "package.json").write_text('{"name":"fixture"}', encoding="utf-8")
    (source / "package-lock.json").write_text('{"lockfileVersion":3}', encoding="utf-8")
    (source / "default" / "config.yaml").write_text("listen: false\n", encoding="utf-8")
    (source / "public" / "app.js").write_text("app-v1", encoding="utf-8")
    (source / "src" / "index.js").write_text("source-v1", encoding="utf-8")
    (source / "node_modules" / "marker.js").write_text("module-v1", encoding="utf-8")
    return source


class PiSceneIsolationIntegrityTests(unittest.TestCase):
    def test_staged_tree_reverifies_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            manifest = stage_isolated_sillytavern(_source(root), target)
            self.assertEqual(manifest["schema_version"], "cera.pi_scene.isolated_sillytavern_copy.v2")
            self.assertEqual(verify_isolated_sillytavern(target), manifest)

    def test_modified_executable_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            stage_isolated_sillytavern(_source(root), target)
            (target / "server.js").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(StateConflictError, "tree changed"):
                verify_isolated_sillytavern(target)

    def test_added_user_data_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            stage_isolated_sillytavern(_source(root), target)
            (target / "data" / "user.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(StateConflictError, "protected material"):
                verify_isolated_sillytavern(target)

    def test_added_code_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            stage_isolated_sillytavern(_source(root), target)
            (target / "src" / "extra.js").write_text("extra", encoding="utf-8")
            with self.assertRaisesRegex(StateConflictError, "copied-file count changed"):
                verify_isolated_sillytavern(target)


if __name__ == "__main__":
    unittest.main()
