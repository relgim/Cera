from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from pro_review_cycle_core import (
    ACCEPTED_RESPONSE_RELATIVE_PATH,
    DEFAULT_REPOSITORY_ROOT,
    EXPECTED_RESPONSE_RELATIVE_PATH,
    FAILED_PRE_MANIFEST_ABSENT_ARTIFACTS,
    FAILED_PRE_MANIFEST_TOMBSTONE_SCHEMA,
    FAILED_PUBLICATION_RECEIPT_SCHEMA,
    JOB4_AUTHORIZATION_SCHEMA,
    JOB4_RESULT_SCHEMA,
    JOB4_RESULT_SCHEMA_V3,
    MANIFEST_SCHEMA,
    MANIFEST_SCHEMA_V3,
    MANIFEST_SCHEMA_V4,
    RECEIPT_SCHEMA,
    SPEC_SCHEMA,
    SPEC_SCHEMA_V3,
    SPEC_SCHEMA_V4,
    SEQUENCE_AUTHORITY_ACTIVATION_SCHEMA,
    SEQUENCE_CLAIM_SCHEMA,
    SEQUENCE_CLAIM_DISPOSITION_SCHEMA,
    STATE_JOB4_IN_PROGRESS,
    STATE_RESPONSE_PENDING,
    STATE_REVIEW_CONSUMED,
    STATE_SCHEMA,
    CycleError,
    ResponseNotReady,
    canonical_json_bytes,
    acquire_sequence_claim,
    activate_sequence_authority_from_repository,
    complete_job4,
    consume_response,
    cycle_status,
    immutable_write,
    latest_consumed_cycle,
    publish_cycle,
    read_json,
    record_sequence_disposition,
    record_trigger,
    recover_cycle,
    wait_and_consume,
    validate_failed_pre_manifest_predecessors,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="CERA repository-local overlapped ChatGPT Pro review cycle"
    )
    commands = value.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("--cycle-directory", type=Path, required=True)
    publish.add_argument("--spec", type=Path, required=True)
    trigger = commands.add_parser("record-trigger")
    trigger.add_argument("--cycle-directory", type=Path, required=True)
    trigger.add_argument("--target-id", required=True)
    trigger.add_argument("--app-result-json", required=True)
    trigger.add_argument("--message-sha256", required=True)
    complete = commands.add_parser("complete-job4")
    complete.add_argument("--cycle-directory", type=Path, required=True)
    complete.add_argument("--stability-delay-ms", type=int, default=250)
    consume = commands.add_parser("consume")
    consume.add_argument("--cycle-directory", type=Path, required=True)
    consume.add_argument("--stability-delay-ms", type=int, default=250)
    wait = commands.add_parser("wait-consume")
    wait.add_argument("--cycle-directory", type=Path, required=True)
    wait.add_argument("--max-polls", type=int, default=20)
    wait.add_argument("--poll-seconds", type=float, default=15)
    wait.add_argument("--stability-delay-ms", type=int, default=250)
    recover = commands.add_parser("recover")
    recover.add_argument("--cycle-directory", type=Path, required=True)
    status = commands.add_parser("status")
    status.add_argument("--cycle-directory", type=Path, required=True)
    commands.add_parser("latest-consumed")
    commands.add_parser("activate-sequence-authority")
    claim = commands.add_parser("acquire-sequence-claim")
    claim.add_argument("--claim", type=Path, required=True)
    disposition = commands.add_parser("record-sequence-disposition")
    disposition.add_argument("--disposition", type=Path, required=True)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "publish":
            result = publish_cycle(arguments.cycle_directory, arguments.spec)
            marker = "CERA_REVIEW_CYCLE_PUBLISHED_JOB4_STARTED"
        elif arguments.command == "record-trigger":
            result = record_trigger(
                arguments.cycle_directory,
                target_id=arguments.target_id,
                app_result_json=arguments.app_result_json,
                message_sha256=arguments.message_sha256,
            )
            marker = "CERA_REVIEW_TRIGGER_ATTESTATION_RECORDED"
        elif arguments.command == "complete-job4":
            result = complete_job4(
                arguments.cycle_directory,
                stability_delay_milliseconds=arguments.stability_delay_ms,
            )
            marker = "CERA_JOB4_COMPLETE_RESPONSE_PENDING"
        elif arguments.command == "consume":
            result = consume_response(
                arguments.cycle_directory,
                stability_delay_milliseconds=arguments.stability_delay_ms,
            )
            marker = "CERA_PRO_RESPONSE_CONSUMED_ADVISORY"
        elif arguments.command == "wait-consume":
            result = wait_and_consume(
                arguments.cycle_directory,
                max_polls=arguments.max_polls,
                poll_seconds=arguments.poll_seconds,
                stability_delay_milliseconds=arguments.stability_delay_ms,
            )
            marker = "CERA_PRO_RESPONSE_CONSUMED_ADVISORY"
        elif arguments.command == "recover":
            result = recover_cycle(arguments.cycle_directory)
            marker = "CERA_REVIEW_CYCLE_RECOVERED"
        elif arguments.command == "status":
            result = cycle_status(arguments.cycle_directory)
            marker = "CERA_REVIEW_CYCLE_STATUS"
        elif arguments.command == "latest-consumed":
            result = latest_consumed_cycle()
            marker = "CERA_LATEST_CONSUMED_REVIEW_CYCLE"
        elif arguments.command == "activate-sequence-authority":
            result = activate_sequence_authority_from_repository()
            marker = "CERA_SEQUENCE_AUTHORITY_ACTIVATED"
        elif arguments.command == "acquire-sequence-claim":
            result = acquire_sequence_claim(read_json(arguments.claim.resolve(strict=True)))
            marker = "CERA_SEQUENCE_CLAIM_ACQUIRED"
        else:
            result = record_sequence_disposition(
                read_json(arguments.disposition.resolve(strict=True))
            )
            marker = "CERA_SEQUENCE_DISPOSITION_RECORDED"
        print(marker)
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0
    except ResponseNotReady as exc:
        print(f"CERA_PRO_RESPONSE_NOT_READY: {exc}", file=sys.stderr)
        return 3
    except CycleError as exc:
        print(f"CERA_REVIEW_CYCLE_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
