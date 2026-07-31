from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from cera.runtime import HanezawaHumanTestWorld


ROOT = Path(__file__).resolve().parents[1]


class HumanTestWorldTests(unittest.TestCase):
    def test_initialize_and_reset_keep_v1_2_doorway_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cera-human-world-test-") as temp:
            database = Path(temp) / "human.sqlite3"
            first = HanezawaHumanTestWorld.initialize(ROOT, database)
            branch = first.store.get_branch(first.branch_id)
            self.assertEqual(branch.generation, 0)
            self.assertIsNone(branch.head_artifact_id)
            self.assertEqual(first.store.visible_artifact_ids(first.branch_id), ())
            self.assertEqual(
                first.store.get_world_genesis_revision(first.world_id),
                first.compiled.manifest.revision_id,
            )
            self.assertEqual(first.store.integrity_check(), ("ok",))
            self.assertEqual(first.store.foreign_key_check(), ())
            first_hash = first.database_path.stat().st_size

            reset = HanezawaHumanTestWorld.initialize(
                ROOT,
                database,
                replace=True,
            )
            reset_branch = reset.store.get_branch(reset.branch_id)
            self.assertEqual(reset_branch.generation, 0)
            self.assertIsNone(reset_branch.head_artifact_id)
            self.assertEqual(reset.store.visible_artifact_ids(reset.branch_id), ())
            self.assertEqual(reset.database_path.stat().st_size, first_hash)


if __name__ == "__main__":
    unittest.main()
