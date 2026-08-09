from __future__ import annotations

import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.writer_view import WriterViewInputV1, WriterViewMaterializer


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_MODULE = ROOT / "integrations" / "pi" / "cera-scene-context.ts"


def _node_context_plan(view_root: Path) -> dict[str, object]:
    script = """
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const module = await import(pathToFileURL(process.argv[1]).href);
const control = readFileSync(process.argv[2], 'utf8');
const manifest = JSON.parse(readFileSync(process.argv[3], 'utf8'));
const paths = ['MANIFEST.json', ...manifest.files.map((value) => value.path)];
console.log(JSON.stringify(module.writerContextPlan(control, paths)));
"""
    completed = subprocess.run(
        [
            "node",
            "--no-warnings",
            "--experimental-strip-types",
            "--input-type=module",
            "--eval",
            script,
            str(CONTEXT_MODULE),
            str(view_root / "zz_CURRENT_TURN_AUTHORITY.json"),
            str(view_root / "MANIFEST.json"),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return json.loads(completed.stdout)


class PiAdultContextIntegrationTests(unittest.TestCase):
    def test_node_route_plan_loads_adult_handoff_without_response_sequence(self) -> None:
        with TemporaryDirectory() as temporary:
            view = WriterViewMaterializer(Path(temporary).resolve()).materialize(
                WriterViewInputV1(
                    world_id="world:test",
                    branch_id="branch:main",
                    scene_id="scene:test",
                    turn_id="turn:test",
                    candidate_id="candidate:test",
                    route=SceneRoute.ADULT,
                    user_prompt="Continue from the authorized adult handoff.",
                    primary_authority={
                        "handoff_key": "adult_boundary",
                        "non_graphic_sequence": "The adult logic owner continues.",
                    },
                    current_state={"public_scene_state": "A private room."},
                    characters={"character:hana": {"name": "Hana"}},
                    relationships={},
                    recent_prose=(),
                    relevant_memories={},
                    voice_examples={},
                    craft_index={"families": []},
                    accepted_records=(),
                )
            )
            self.assertTrue((view.root / "ADULT_HANDOFF.json").is_file())
            self.assertFalse((view.root / "RESPONSE_SEQUENCE.json").exists())
            plan = _node_context_plan(view.root)
            self.assertEqual(plan["authorityPath"], "ADULT_HANDOFF.json")
            self.assertNotIn("ADULT_HANDOFF.json", plan["semanticPaths"])


if __name__ == "__main__":
    unittest.main()
