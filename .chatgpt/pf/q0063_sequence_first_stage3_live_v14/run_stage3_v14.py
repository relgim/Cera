from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
V14_ROOT = ROOT / ".chatgpt/pf/q0063_sequence_first_stage3_live_v14"
CORRECTION_ROOT = (
    ROOT / ".chatgpt/pf/q0063_sequence_first_stage3_v8_harness_correction"
)
sys.path.insert(0, str(CORRECTION_ROOT))
sys.path.insert(0, str(V14_ROOT))

from governed_runner_builder_v14 import build_runner_namespace, prove_source


RUN_ID = "2026-08-06-cera-sequence-first-stage3-live-v14"
SOURCE_COMMIT = "471a849fd00d59164b3a440d9aadf052bc3e2b67"
SOURCE_TREE = "a500ccbd44324d329e4b344ff30471a69974a72e"
RUN_AUTHORITY_SHA256 = "feac3d49fafda0523eaa181561f692c4e66060f675dc7caf1ebd2229e3630ce7"


def load_v14():
    return build_runner_namespace(
        root=ROOT,
        wrapper_path=Path(__file__).resolve(),
        run_id=RUN_ID,
        source_commit=SOURCE_COMMIT,
        source_tree=SOURCE_TREE,
        world_id="world-q0063-stage3-v14",
        session_id="q0063-stage3-v14",
        result_filename="GOAL_3_STAGE3_V14_RESULT.json",
        initial_roadmap_revision="0015",
        run_authority_sha256=RUN_AUTHORITY_SHA256,
    )


def main() -> int:
    prove_source(ROOT, commit=SOURCE_COMMIT, tree=SOURCE_TREE)
    return int(load_v14()["run"]())


if __name__ == "__main__":
    raise SystemExit(main())
