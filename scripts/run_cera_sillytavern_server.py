"""Run the loopback-only CERA development adapter for SillyTavern."""

from __future__ import annotations

import argparse
from pathlib import Path

from cera.runtime import (
    HUMAN_TEST_DATABASE_RELATIVE_PATH,
    HanezawaHumanTestWorld,
)
from cera.sillytavern import (
    CeraSillyTavernAdapter,
    CeraSillyTavernServerConfig,
    LiveSillyTavernTurnExecutor,
    build_server,
)


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / HUMAN_TEST_DATABASE_RELATIVE_PATH,
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5101)
    args = parser.parse_args()
    world = HanezawaHumanTestWorld.open(ROOT, args.database)
    executor = LiveSillyTavernTurnExecutor(
        world,
        diagnostic_report_path=(
            ROOT
            / "runtime"
            / "development"
            / "diagnostics"
            / "CREATOR_CORRECTION_NEEDS.md"
        ),
    )
    adapter = CeraSillyTavernAdapter(world, executor)
    server = build_server(
        adapter,
        CeraSillyTavernServerConfig(host=args.host, port=args.port),
    )
    print(
        f"CERA SillyTavern development adapter listening at "
        f"http://{args.host}:{args.port}/v1",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        executor.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
