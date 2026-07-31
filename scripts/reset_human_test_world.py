"""Create or reset CERA's non-production V1.2 human-test world."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cera.runtime import (
    HUMAN_TEST_DATABASE_RELATIVE_PATH,
    HanezawaHumanTestWorld,
)
from cera.serialization import bytes_sha256


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / HUMAN_TEST_DATABASE_RELATIVE_PATH,
    )
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    world = HanezawaHumanTestWorld.initialize(
        ROOT,
        args.database,
        replace=args.replace,
    )
    branch = world.store.get_branch(world.branch_id)
    result = {
        "status": "ready",
        "production": False,
        "database_path": str(world.database_path),
        "database_sha256": bytes_sha256(world.database_path.read_bytes()),
        "world_id": str(world.world_id),
        "branch_id": str(world.branch_id),
        "genesis_revision_id": str(world.revision_id),
        "generation": branch.generation,
        "head_artifact_id": (
            str(branch.head_artifact_id)
            if branch.head_artifact_id is not None
            else None
        ),
        "visible_artifact_count": len(
            world.store.visible_artifact_ids(world.branch_id)
        ),
        "integrity_check": world.store.integrity_check(),
        "foreign_key_check": world.store.foreign_key_check(),
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
