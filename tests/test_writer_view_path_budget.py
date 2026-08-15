import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.writer_view import (
    WriterViewInputV1,
    WriterViewMaterializer,
    verify_writer_view,
)


class WriterViewPathBudgetTests(unittest.TestCase):
    def test_staging_name_preserves_windows_path_budget(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            padding = "x" * max(8, 81 - len(str(base)) - 1)
            root = base / padding
            view = WriterViewMaterializer(root).materialize(
                WriterViewInputV1(
                    world_id="world-hanezawa-chat",
                    branch_id="branch-chat",
                    scene_id="scene-adult",
                    turn_id="turn-adult",
                    candidate_id="candidate:adult:path-budget",
                    route=SceneRoute.ADULT,
                    user_prompt="Continue the authorized adult scene.",
                    primary_authority={"causal_direction": "Continue."},
                    current_state={"public_scene_state": "Sakura is present."},
                    characters={
                        "character:sakura_hanezawa": {"character_id": "character:sakura_hanezawa"}
                    },
                    relationships={
                        "character:sakura_hanezawa": {"character_id": "character:sakura_hanezawa"}
                    },
                    recent_prose=(),
                    relevant_memories={
                        "character:sakura_hanezawa": {"character_id": "character:sakura_hanezawa"}
                    },
                    voice_examples={},
                    craft_index={},
                    accepted_records=(),
                )
            )
            self.assertGreaterEqual(len(str(root)), 81)
            self.assertEqual(view, verify_writer_view(view.root))
