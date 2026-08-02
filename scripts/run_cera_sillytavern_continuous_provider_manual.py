#!/usr/bin/env python3
"""Launch the isolated provider-backed Continuous V3 manual route."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cera.sillytavern.manual_routes import PROVIDER_BACKED_MANUAL_ROUTE
from scripts import run_cera_sillytavern_continuous_manual as manual


def main(argv: Sequence[str] | None = None) -> int:
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument(
        "--transport-mode",
        choices=("non_network_fake_ports", "external_provider"),
        required=True,
    )
    bootstrap.add_argument("--provider-activation", type=Path)
    bootstrap.add_argument("--cycle-directory", type=Path)
    bootstrap.add_argument("--expected-checkpoint-sha")
    bootstrap.add_argument("--expected-cycle-id")
    bootstrap.add_argument("--expected-cycle-sequence", type=int)
    bootstrap.add_argument("--expected-job4-task-id")
    bootstrap.add_argument("--expected-authorization-sha256")
    options, remaining = bootstrap.parse_known_args(argv)
    authority_values = (
        options.cycle_directory,
        options.expected_checkpoint_sha,
        options.expected_cycle_id,
        options.expected_cycle_sequence,
        options.expected_job4_task_id,
        options.expected_authorization_sha256,
    )
    if any(value is not None for value in authority_values) and not all(
        value is not None for value in authority_values
    ):
        bootstrap.error("provider cycle authority must be complete")
    provider_cycle_authority = (
        None
        if not all(value is not None for value in authority_values)
        else {
            "cycle_directory": str(options.cycle_directory.resolve()),
            "expected_checkpoint_sha": options.expected_checkpoint_sha,
            "expected_cycle_id": options.expected_cycle_id,
            "expected_cycle_sequence": options.expected_cycle_sequence,
            "expected_job4_task_id": options.expected_job4_task_id,
            "expected_authorization_sha256": (
                options.expected_authorization_sha256
            ),
        }
    )
    manual.configure_manual_execution(
        PROVIDER_BACKED_MANUAL_ROUTE,
        transport_mode=options.transport_mode,
        provider_activation=options.provider_activation,
        provider_cycle_authority=provider_cycle_authority,
    )
    return manual.main(list(remaining))


if __name__ == "__main__":
    raise SystemExit(main())
