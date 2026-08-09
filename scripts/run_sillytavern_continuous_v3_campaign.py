"""Run the identity-bound two-pass Continuous V3 SillyTavern qualification.

The parent campaign launches every immutable run in a new Python process.  A
single run owns a fresh database copy, continuous world, branch, provider
sessions, adapter, and loopback HTTP server.  There is no retry or fallback.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from threading import Thread
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.codex_stored import CodexContinuousStoredSessionPort
from cera.continuous.contracts import (
    RichPlannerSequenceV1,
    ValidatorFinalizationPackageV1,
)
from cera.continuous.prompting import (
    CONTINUOUS_PLANNER_PROMPT_VERSION,
    CONTINUOUS_VALIDATOR_PROMPT_VERSION,
    PLANNER_STABLE_INSTRUCTIONS,
    VALIDATOR_STABLE_INSTRUCTIONS,
)
from cera.continuous.scripted_job4 import (
    SCRIPTED_JOB4_FIXTURE_ID,
    SCRIPTED_JOB4_FIXTURE_SHA256,
    ScriptedJob4FixtureRuntime,
)
from cera.continuous.sessions import (
    CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_SHA256,
)
from cera.continuous.provider import CONTINUOUS_DEEPSEEK_PROMPT_VERSION
from cera.continuous.record_policy import PERSISTENCE_POLICY_SHA256
from cera.continuous.sessions import (
    ContinuousSessionCoordinator,
    ContinuousSessionRole,
    InMemoryContinuousStoredSessionPort,
    assert_separate_role_sessions,
)
from cera.continuous.thread_lineage import ContinuousThreadLineageLedger
from cera.continuous.world import ContinuousWorldStore
from cera.creator_review import CreatorReviewAction
from cera.providers.codex_worker import _BASE_INSTRUCTIONS_BY_ROLE
from cera.provider_dispatch_guard import assert_provider_dispatch_allowed
from cera.reasoner_session import OpenAICodexStoredThreadBackend
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    re_is_sha256,
    text_sha256,
)
from cera.sillytavern.campaign import (
    CONTINUOUS_V3_CAMPAIGN_CONFIGURATION_V2_SCHEMA,
    CAMPAIGN_TOTAL_CALL_CEILING,
    CONTINUOUS_V3_CALL_SCHEDULE,
    CONTINUOUS_V3_RUN_IDENTITIES,
    CONTINUOUS_V3_V1_CAMPAIGN_ID,
    CONTINUOUS_V3_V2_CAMPAIGN_ID,
    CONTINUOUS_V3_V2_CAMPAIGN_TOTAL_CALL_CEILING,
    CONTINUOUS_V3_V2_CODEX_FAMILY_CALL_CEILING,
    CONTINUOUS_V3_V2_DEEPSEEK_CALL_CEILING,
    PROVIDER_FREE_PARTIAL_FAILURE_FIXTURE,
    PROVIDER_FREE_PARTIAL_FAILURE_FIXTURE_SHA256,
    CampaignRunRecord,
    CampaignRunState,
    ContinuousV3TwoRunCampaign,
    CONTINUOUS_V3_V2_RUN_IDENTITIES,
    validate_v2_campaign_configuration,
)
from cera.sillytavern.continuous_test import (
    CONTINUOUS_V3_TEST_FIXTURE,
    ContinuousSillyTavernTestAdapter,
)
from cera.sillytavern.models import CERA_CONTINUOUS_V3_TEST_MODEL
from cera.sillytavern.provider_authority import (
    CONTINUOUS_PROVIDER_MODELS,
    validate_provider_activation,
)
from cera.sillytavern.server import CeraSillyTavernServerConfig, build_server
try:
    from tools.pro_review_cycle_core import (
        load_v2_manifest,
        validate_publication,
        validate_state_view,
        validate_trigger_receipt,
    )
except ModuleNotFoundError:
    # Direct script execution starts with scripts/ on sys.path.  Bind the
    # repository-owned cycle validator rather than accepting a weaker duplicate.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.pro_review_cycle_core import (
        load_v2_manifest,
        validate_publication,
        validate_state_view,
        validate_trigger_receipt,
    )

if __package__:
    from scripts.run_continuous_planner_validator_job4 import (
        BRANCH_ID,
        ROOT,
        WORLD_ID,
        JobHarness,
        _terminalize_known_thread_sessions,
        compatibility,
        seed_world,
    )
else:
    from run_continuous_planner_validator_job4 import (
        BRANCH_ID,
        ROOT,
        WORLD_ID,
        JobHarness,
        _terminalize_known_thread_sessions,
        compatibility,
        seed_world,
    )


CAMPAIGN_ID = CONTINUOUS_V3_V2_CAMPAIGN_ID
HISTORICAL_CAMPAIGN_ID = CONTINUOUS_V3_V1_CAMPAIGN_ID
PROFILE_ID = "cera.sillytavern.continuous_v3_test.v1"
FIXED_PORT = 5113

_EXPECTED_OWNER_BY_CALL = {
    "planner": "planner",
    "deepseek": "composer",
    "validator": "validator",
}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical_bytes(payload) + b"\n")
    temporary.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSON object required on every line: {path}")
        records.append(value)
    return tuple(records)


def _call_owner(label: str) -> str:
    for token, owner in _EXPECTED_OWNER_BY_CALL.items():
        if token in label:
            return owner
    raise ValueError(f"unsupported campaign call label: {label}")


def _provider_invocation_prefix(
    ledger_path: Path,
) -> tuple[tuple[str, ...], tuple[dict[str, Any], ...]]:
    """Recover the conservative consumed-call prefix from a child ledger."""

    if not ledger_path.is_file():
        return (), ()
    events = read_jsonl(ledger_path)
    events_by_call: dict[str, list[dict[str, Any]]] = {}
    call_order: list[str] = []
    for expected_index, event in enumerate(events, start=1):
        call_id = event.get("call_id")
        if (
            not isinstance(call_id, str)
            or not call_id
            or event.get("event_index") != expected_index
            or not isinstance(event.get("owner"), str)
        ):
            raise ValueError("provider ledger call identity is invalid")
        if call_id not in events_by_call:
            call_order.append(call_id)
            events_by_call[call_id] = []
        events_by_call[call_id].append(event)

    unresolved_states = {
        "prepared_not_invoked",
        "worker_started_not_invoked",
        "worker_preflight_not_invoked",
    }
    consumed: list[dict[str, Any]] = []
    for call_id in call_order:
        call_events = events_by_call[call_id]
        invoked = [
            event for event in call_events if event.get("state") == "transport_invoked"
        ]
        if len(invoked) > 1:
            raise ValueError("provider ledger repeats a transport invocation")
        if invoked:
            consumed.append(invoked[0])
        elif call_events[-1].get("state") in unresolved_states:
            # The durable ledger intentionally treats an unresolved prepared or
            # worker-side call as consumed: transport absence was not proven.
            consumed.append(call_events[-1])

    if len(consumed) > len(CONTINUOUS_V3_CALL_SCHEDULE):
        raise ValueError("provider ledger exceeds the exact run schedule")
    calls = CONTINUOUS_V3_CALL_SCHEDULE[: len(consumed)]
    for label, event in zip(calls, consumed, strict=True):
        if event.get("owner") != _call_owner(label):
            raise ValueError("provider ledger owner disagrees with schedule")
    return calls, tuple(consumed)


def _artifact_sha256(path: Path) -> str | None:
    return bytes_sha256(path.read_bytes()) if path.is_file() else None


def _validate_child_call_records(
    raw_calls: Any,
    *,
    consumed_calls: tuple[str, ...],
) -> None:
    """Validate attempted child stages against the consumed ledger prefix."""

    if not isinstance(raw_calls, list):
        raise ValueError("child result call records are malformed")
    if any(not isinstance(record, dict) for record in raw_calls):
        raise ValueError("child result call records are malformed")
    attempted_calls = tuple(record.get("label") for record in raw_calls)
    if attempted_calls != CONTINUOUS_V3_CALL_SCHEDULE[: len(attempted_calls)]:
        raise ValueError("child result attempted-call order changed")
    if (
        len(consumed_calls) > len(attempted_calls)
        or attempted_calls[: len(consumed_calls)] != consumed_calls
    ):
        raise ValueError("child result call records disagree with its ledger")


def _validate_child_reconciliation(
    value: dict[str, Any],
    *,
    run_root: Path,
    expected_run_id: str,
) -> None:
    legacy_fields = {
        "schema_version",
        "run_id",
        "execution_identity_sha256",
        "process_returncode",
        "process_error_type",
        "process_error_message_sha256",
        "child_result_sha256",
        "child_execution_manifest_sha256",
        "provider_ledger_sha256",
        "provider_calls",
        "codex_family_calls",
        "deepseek_calls",
        "calls",
        "call_source",
        "passed",
        "terminal_reason",
        "failure_source",
        "raw_provider_output_retained",
        "reconciliation_sha256",
    }
    v2_fields = legacy_fields | {
        "campaign_configuration_sha256",
        "child_campaign_configuration_file_sha256",
        "transport_mode",
        "stage_invocations",
        "external_provider_calls",
    }
    schema_version = value.get("schema_version")
    if (
        schema_version == "cera.sillytavern_child_run_reconciliation.v1"
        and set(value) != legacy_fields
    ) or (
        schema_version == "cera.sillytavern_child_run_reconciliation.v2"
        and set(value) != v2_fields
    ):
        raise ValueError("child reconciliation fields changed")
    if schema_version not in {
        "cera.sillytavern_child_run_reconciliation.v1",
        "cera.sillytavern_child_run_reconciliation.v2",
    }:
        raise ValueError("child reconciliation schema changed")
    receipt_sha256 = value.get("reconciliation_sha256")
    unsigned = dict(value)
    unsigned.pop("reconciliation_sha256", None)
    if not isinstance(receipt_sha256, str) or receipt_sha256 != canonical_sha256(unsigned):
        raise ValueError("child reconciliation receipt changed")
    if value.get("run_id") != expected_run_id:
        raise ValueError("child reconciliation run identity changed")
    if not re_is_sha256(value.get("execution_identity_sha256")):
        raise ValueError("child reconciliation execution identity is invalid")
    process_returncode = value.get("process_returncode")
    process_error_type = value.get("process_error_type")
    process_error_message_sha256 = value.get("process_error_message_sha256")
    if process_error_type is None:
        if type(process_returncode) is not int or process_error_message_sha256 is not None:
            raise ValueError("child reconciliation process outcome is invalid")
    elif (
        process_returncode is not None
        or not isinstance(process_error_type, str)
        or not process_error_type.strip()
        or not re_is_sha256(process_error_message_sha256)
    ):
        raise ValueError("child reconciliation process failure is invalid")
    calls = value.get("calls")
    if not isinstance(calls, list):
        raise ValueError("child reconciliation call prefix is malformed")
    expected_calls = list(CONTINUOUS_V3_CALL_SCHEDULE[: len(calls)])
    if calls != expected_calls or value.get("provider_calls") != len(calls):
        raise ValueError("child reconciliation call accounting changed")
    if schema_version.endswith(".v2"):
        if (
            not re_is_sha256(value.get("campaign_configuration_sha256"))
            or (
                value.get("child_campaign_configuration_file_sha256") is not None
                and not re_is_sha256(
                    value.get("child_campaign_configuration_file_sha256")
                )
            )
            or value.get("transport_mode")
            not in {"external_provider", "non_network_fake_ports"}
            or value.get("stage_invocations") != len(calls)
            or type(value.get("external_provider_calls")) is not int
            or value["external_provider_calls"] < 0
            or (
                value["transport_mode"] == "non_network_fake_ports"
                and value["external_provider_calls"] != 0
            )
            or (
                value["transport_mode"] == "external_provider"
                and value["external_provider_calls"] != len(calls)
            )
        ):
            raise ValueError("child reconciliation transport accounting changed")
    codex_calls = sum(_call_owner(label) != "composer" for label in calls)
    deepseek_calls = sum(_call_owner(label) == "composer" for label in calls)
    if (
        value.get("codex_family_calls") != codex_calls
        or value.get("deepseek_calls") != deepseek_calls
    ):
        raise ValueError("child reconciliation provider-family accounting changed")
    artifact_paths = {
        "child_result_sha256": run_root / "RUN_RESULT.json",
        "child_execution_manifest_sha256": run_root / "EXECUTION_MANIFEST.json",
        "provider_ledger_sha256": run_root / "PROVIDER_CALL_LEDGER.jsonl",
    }
    if schema_version.endswith(".v2"):
        artifact_paths["child_campaign_configuration_file_sha256"] = (
            run_root / "CAMPAIGN_CONFIGURATION.json"
        )
    for field, path in artifact_paths.items():
        expected = value.get(field)
        actual = _artifact_sha256(path)
        if expected != actual:
            raise ValueError("child reconciliation artifact binding changed")
    if value.get("call_source") not in {
        "child_result_and_provider_ledger",
        "provider_ledger_prefix",
        "no_transport_invocation",
    }:
        raise ValueError("child reconciliation call source changed")
    if (
        type(value.get("passed")) is not bool
        or value.get("raw_provider_output_retained") is not False
        or not isinstance(value.get("terminal_reason"), str)
        or not value["terminal_reason"].strip()
        or value.get("failure_source")
        not in {None, "child_result", "parent_process_launch", "parent_process_exit"}
    ):
        raise ValueError("child reconciliation disposition is invalid")
    child_result = (
        read_json(run_root / "RUN_RESULT.json")
        if (run_root / "RUN_RESULT.json").is_file()
        else None
    )
    derived_passed = bool(
        process_error_type is None
        and process_returncode == 0
        and child_result is not None
        and child_result.get("status") == "passed"
        and tuple(calls) == CONTINUOUS_V3_CALL_SCHEDULE
        and (
            schema_version.endswith(".v1")
            or value.get("child_campaign_configuration_file_sha256") is not None
        )
    )
    if value["passed"] is not derived_passed:
        raise ValueError("child reconciliation pass status is not derived")
    if derived_passed and (
        value["terminal_reason"] != "qualified" or value["failure_source"] is not None
    ):
        raise ValueError("passing child reconciliation has a failure disposition")
    if not derived_passed and value["failure_source"] is None:
        raise ValueError("failed child reconciliation lacks a failure source")


def reconcile_child_run(
    *,
    run_root: Path,
    run_id: str,
    execution_identity_sha256: str,
    process_returncode: int | None,
    process_error: BaseException | None = None,
    campaign_configuration_sha256: str | None = None,
    transport_mode: str | None = None,
) -> dict[str, Any]:
    """Freeze parent-owned truth even when a child never writes RUN_RESULT."""

    if (process_returncode is None) is not (process_error is not None):
        raise ValueError("child process outcome is ambiguous")
    result_path = run_root / "RUN_RESULT.json"
    manifest_path = run_root / "EXECUTION_MANIFEST.json"
    ledger_path = run_root / "PROVIDER_CALL_LEDGER.jsonl"
    result = read_json(result_path) if result_path.is_file() else None
    calls, invoked = _provider_invocation_prefix(ledger_path)
    if result is not None:
        if result.get("run_id") != run_id or result.get("status") not in {
            "passed",
            "failed",
        }:
            raise ValueError("child result identity or terminal state changed")
        if result.get("provider_calls") != len(invoked):
            raise ValueError("child result provider count disagrees with its ledger")
        raw_calls = result.get("calls")
        if raw_calls is not None:
            _validate_child_call_records(raw_calls, consumed_calls=calls)
    passed = bool(
        process_error is None
        and process_returncode == 0
        and result is not None
        and result.get("status") == "passed"
        and calls == CONTINUOUS_V3_CALL_SCHEDULE
    )
    child_configuration_path = run_root / "CAMPAIGN_CONFIGURATION.json"
    if campaign_configuration_sha256 is not None and not child_configuration_path.is_file():
        terminal_reason = "ChildCampaignConfigurationMissing"
        failure_source = "child_result" if result is not None else "parent_process_exit"
    elif result is not None and isinstance(result.get("error_type"), str):
        terminal_reason = result["error_type"]
        failure_source = "child_result"
    elif process_error is not None:
        terminal_reason = type(process_error).__name__
        failure_source = "parent_process_launch"
    elif result is None:
        terminal_reason = "ChildResultMissing"
        failure_source = "parent_process_exit"
    elif process_returncode != 0:
        terminal_reason = "ChildProcessExit"
        failure_source = "parent_process_exit"
    elif result.get("status") != "passed":
        terminal_reason = "ChildResultFailed"
        failure_source = "child_result"
    elif calls != CONTINUOUS_V3_CALL_SCHEDULE:
        terminal_reason = "IncompleteProviderSchedule"
        failure_source = "child_result"
    else:
        terminal_reason = "qualified"
        failure_source = None
    if (campaign_configuration_sha256 is None) is not (transport_mode is None):
        raise ValueError("child reconciliation V2 identity is incomplete")
    schema_version = (
        "cera.sillytavern_child_run_reconciliation.v1"
        if campaign_configuration_sha256 is None
        else "cera.sillytavern_child_run_reconciliation.v2"
    )
    payload: dict[str, Any] = {
        "schema_version": schema_version,
        "run_id": run_id,
        "execution_identity_sha256": execution_identity_sha256,
        "process_returncode": process_returncode,
        "process_error_type": (
            None if process_error is None else type(process_error).__name__
        ),
        "process_error_message_sha256": (
            None if process_error is None else text_sha256(str(process_error))
        ),
        "child_result_sha256": _artifact_sha256(result_path),
        "child_execution_manifest_sha256": _artifact_sha256(manifest_path),
        "provider_ledger_sha256": _artifact_sha256(ledger_path),
        "provider_calls": len(calls),
        "codex_family_calls": sum(
            _call_owner(label) != "composer" for label in calls
        ),
        "deepseek_calls": sum(_call_owner(label) == "composer" for label in calls),
        "calls": list(calls),
        "call_source": (
            "child_result_and_provider_ledger"
            if result is not None and result.get("calls") is not None
            else "provider_ledger_prefix"
            if calls
            else "no_transport_invocation"
        ),
        "passed": passed,
        "terminal_reason": terminal_reason,
        "failure_source": failure_source,
        "raw_provider_output_retained": False,
    }
    if campaign_configuration_sha256 is not None:
        if (
            not re_is_sha256(campaign_configuration_sha256)
            or transport_mode not in {
                "external_provider",
                "non_network_fake_ports",
            }
        ):
            raise ValueError("child reconciliation V2 authority is invalid")
        payload.update(
            {
                "campaign_configuration_sha256": (
                    campaign_configuration_sha256
                ),
                "child_campaign_configuration_file_sha256": _artifact_sha256(
                    run_root / "CAMPAIGN_CONFIGURATION.json"
                ),
                "transport_mode": transport_mode,
                "stage_invocations": len(calls),
                "external_provider_calls": (
                    0
                    if transport_mode == "non_network_fake_ports"
                    else len(calls)
                ),
            }
        )
    payload["reconciliation_sha256"] = canonical_sha256(payload)
    run_root.mkdir(parents=True, exist_ok=True)
    reconciliation_path = run_root / "PARENT_RUN_RECONCILIATION.json"
    if reconciliation_path.exists():
        raise FileExistsError("refusing to overwrite immutable child reconciliation")
    write_json(reconciliation_path, payload)
    _validate_child_reconciliation(payload, run_root=run_root, expected_run_id=run_id)
    return payload


def recover_prior_campaign(
    prior_root: Path,
    *,
    execution_identity_sha256: str,
) -> tuple[ContinuousV3TwoRunCampaign, dict[str, Any]]:
    """Recover terminal history from immutable per-run evidence.

    Run 001 exposed that the original parent result omitted a dispatched call
    when the provider completed but domain validation failed.  Recovery trusts
    the append-only provider ledger and per-run result, records the discrepancy,
    and never rewrites the historical files.
    """

    root = prior_root.resolve()
    campaign_path = root / "CAMPAIGN_RESULT.json"
    manifest_path = root / "EXECUTION_MANIFEST.json"
    prior = read_json(campaign_path)
    manifest = read_json(manifest_path)
    if prior.get("campaign_id") != HISTORICAL_CAMPAIGN_ID:
        raise ValueError("prior campaign identity changed")
    prior_execution_identity = manifest.get("execution_identity_sha256")
    if (
        not isinstance(prior_execution_identity, str)
        or not re_is_sha256(prior_execution_identity)
        or prior.get("execution_identity_sha256") != prior_execution_identity
    ):
        raise ValueError("prior campaign execution manifest is invalid")
    prior_campaign = prior.get("campaign")
    if not isinstance(prior_campaign, dict):
        raise ValueError("prior campaign state is missing")
    raw_runs = prior_campaign.get("runs")
    if not isinstance(raw_runs, list) or not raw_runs:
        raise ValueError("prior campaign has no recoverable runs")
    if len(raw_runs) >= len(CONTINUOUS_V3_RUN_IDENTITIES):
        raise ValueError("prior campaign exhausted all immutable run identities")
    prior_schema = prior.get("schema_version")
    if prior_schema not in {
        None,
        "cera.sillytavern_continuous_v3_campaign_result.v1",
        "cera.sillytavern_continuous_v3_campaign_result.v2",
    }:
        raise ValueError("prior campaign result schema changed")
    local_reconciliation_bindings: dict[str, dict[str, Any]] = {}
    if prior_schema == "cera.sillytavern_continuous_v3_campaign_result.v2":
        raw_bindings = prior.get("local_run_reconciliations")
        if not isinstance(raw_bindings, list):
            raise ValueError("prior campaign reconciliation index is missing")
        if (
            prior.get("local_run_reconciliations_sha256")
            != canonical_sha256(raw_bindings)
        ):
            raise ValueError("prior campaign reconciliation index hash changed")
        for binding in raw_bindings:
            expected_binding_fields = {
                "run_id",
                "relative_path",
                "file_sha256",
                "receipt_sha256",
            }
            if (
                not isinstance(binding, dict)
                or set(binding) != expected_binding_fields
                or binding.get("run_id") in local_reconciliation_bindings
                or not re_is_sha256(binding.get("file_sha256"))
                or not re_is_sha256(binding.get("receipt_sha256"))
            ):
                raise ValueError("prior campaign reconciliation index is malformed")
            run_id = binding["run_id"]
            if run_id not in CONTINUOUS_V3_RUN_IDENTITIES:
                raise ValueError("prior campaign reconciliation run is unknown")
            expected_relative = (
                f"runs/{run_id}/PARENT_RUN_RECONCILIATION.json"
            )
            if binding.get("relative_path") != expected_relative:
                raise ValueError("prior campaign reconciliation path changed")
            local_reconciliation_bindings[run_id] = binding
    prior_recovery_path = root / "PRIOR_CAMPAIGN_RECOVERY.json"
    inherited_run_roots: dict[str, Path] = {}
    inherited_run_entries: dict[str, dict[str, Any]] = {}
    prior_recovery_sha256 = None
    if prior_recovery_path.is_file():
        prior_recovery = read_json(prior_recovery_path)
        prior_recovery_sha256 = bytes_sha256(prior_recovery_path.read_bytes())
        inherited = prior_recovery.get("runs")
        if not isinstance(inherited, list):
            raise ValueError("prior campaign recovery run index is malformed")
        for item in inherited:
            if not isinstance(item, dict):
                raise ValueError("prior campaign recovery run entry is malformed")
            run_id = item.get("run_id")
            evidence_root = item.get("evidence_root")
            if not isinstance(run_id, str) or not isinstance(evidence_root, str):
                raise ValueError("prior campaign recovery evidence root is invalid")
            inherited_run_roots[run_id] = Path(evidence_root).resolve()
            inherited_run_entries[run_id] = item

    recovered: list[CampaignRunRecord] = []
    recovery_runs: list[dict[str, Any]] = []
    total_calls = 0
    for index, raw in enumerate(raw_runs):
        if not isinstance(raw, dict):
            raise ValueError("prior campaign run is malformed")
        run_id = CONTINUOUS_V3_RUN_IDENTITIES[index]
        if raw.get("run_id") != run_id or raw.get("state") not in {"passed", "failed"}:
            raise ValueError("prior campaign run identity or terminal state changed")
        local_run_root = root / "runs" / run_id
        run_root = (
            local_run_root
            if local_run_root.is_dir()
            else inherited_run_roots.get(run_id, local_run_root)
        )
        if not run_root.is_dir():
            raise ValueError("prior immutable run evidence is unavailable")
        result_path = run_root / "RUN_RESULT.json"
        ledger_path = run_root / "PROVIDER_CALL_LEDGER.jsonl"
        run_manifest_path = run_root / "EXECUTION_MANIFEST.json"
        reconciliation_path = run_root / "PARENT_RUN_RECONCILIATION.json"
        inherited_entry = inherited_run_entries.get(run_id)
        reconciliation_sha256 = None
        if reconciliation_path.is_file():
            reconciliation = read_json(reconciliation_path)
            _validate_child_reconciliation(
                reconciliation,
                run_root=run_root,
                expected_run_id=run_id,
            )
            reconciliation_sha256 = bytes_sha256(reconciliation_path.read_bytes())
            if local_run_root.is_dir() and prior_schema == (
                "cera.sillytavern_continuous_v3_campaign_result.v2"
            ):
                binding = local_reconciliation_bindings.pop(run_id, None)
                if (
                    binding is None
                    or binding["file_sha256"] != reconciliation_sha256
                    or binding["receipt_sha256"]
                    != reconciliation["reconciliation_sha256"]
                ):
                    raise ValueError(
                        "parent child reconciliation changed after campaign publication"
                    )
            if (
                reconciliation.get("execution_identity_sha256") is None
                or not re_is_sha256(reconciliation["execution_identity_sha256"])
            ):
                raise ValueError("prior child reconciliation execution identity is invalid")
            expected_passed = raw.get("state") == "passed"
            if reconciliation.get("passed") is not expected_passed:
                raise ValueError(
                    "prior child reconciliation disagrees with campaign terminal state"
                )
            calls = tuple(reconciliation["calls"])
            if tuple(raw.get("calls", ())) != calls:
                raise ValueError(
                    "prior campaign calls disagree with child reconciliation"
                )
            call_source = f"parent_child_reconciliation:{reconciliation['call_source']}"
            old_identity = reconciliation["execution_identity_sha256"]
            result_sha256 = reconciliation["child_result_sha256"]
            ledger_sha256 = reconciliation["provider_ledger_sha256"]
            run_manifest_sha256 = reconciliation[
                "child_execution_manifest_sha256"
            ]
            immutable_ledger_call_count = reconciliation["provider_calls"]
        else:
            if local_run_root.is_dir() and prior_schema == (
                "cera.sillytavern_continuous_v3_campaign_result.v2"
            ):
                raise ValueError("published local run lacks parent reconciliation")
            result = read_json(result_path)
            run_manifest = read_json(run_manifest_path)
            if result.get("run_id") != run_id or result.get("status") != raw.get(
                "state"
            ):
                raise ValueError(
                    "prior run result disagrees with terminal campaign history"
                )
            calls, invoked = _provider_invocation_prefix(ledger_path)
            if result.get("provider_calls") != len(invoked):
                raise ValueError(
                    "prior run provider count disagrees with its immutable ledger"
                )
            raw_result_calls = result.get("calls")
            if raw_result_calls is None:
                call_source = "recovered_from_transport_invoked_prefix"
            else:
                _validate_child_call_records(
                    raw_result_calls,
                    consumed_calls=calls,
                )
                call_source = "run_result_call_records"
            old_identity = run_manifest.get("execution_identity_sha256")
            if not isinstance(old_identity, str) or not re_is_sha256(old_identity):
                raise ValueError("prior run execution identity is invalid")
            result_sha256 = bytes_sha256(result_path.read_bytes())
            ledger_sha256 = bytes_sha256(ledger_path.read_bytes())
            run_manifest_sha256 = bytes_sha256(run_manifest_path.read_bytes())
            immutable_ledger_call_count = len(invoked)
        if inherited_entry is not None:
            expected_hashes = {
                "run_result_sha256": result_sha256,
                "provider_ledger_sha256": ledger_sha256,
                "execution_manifest_sha256": run_manifest_sha256,
                "parent_run_reconciliation_sha256": reconciliation_sha256,
            }
            if any(inherited_entry.get(key) != value for key, value in expected_hashes.items()):
                raise ValueError("inherited immutable run evidence hash changed")
        total_calls += len(calls)
        state = CampaignRunState(raw["state"])
        recovered.append(
            CampaignRunRecord(
                run_id=run_id,
                state=state,
                execution_identity_sha256=old_identity,
                calls=calls,
                terminal_reason=str(raw.get("terminal_reason") or "historical_terminal"),
            )
        )
        recovery_runs.append(
            {
                "run_id": run_id,
                "evidence_root": str(run_root),
                "state": state.value,
                "execution_identity_sha256": old_identity,
                "calls": list(calls),
                "call_source": call_source,
                "run_result_sha256": result_sha256,
                "provider_ledger_sha256": ledger_sha256,
                "execution_manifest_sha256": run_manifest_sha256,
                "parent_run_reconciliation_sha256": reconciliation_sha256,
                "recorded_parent_call_count": len(raw.get("calls", ())),
                "immutable_ledger_call_count": immutable_ledger_call_count,
            }
        )
    if local_reconciliation_bindings:
        raise ValueError("prior campaign reconciliation index contains unused entries")
    if total_calls > CAMPAIGN_TOTAL_CALL_CEILING:
        raise ValueError("recovered campaign exceeds the provider-call ceiling")
    same_execution_identity = (
        prior.get("execution_identity_sha256") == execution_identity_sha256
    )
    recovered_consecutive_passes = 0
    if same_execution_identity:
        for record in recovered:
            if record.state is CampaignRunState.PASSED:
                recovered_consecutive_passes += 1
            else:
                recovered_consecutive_passes = 0
    if recovered_consecutive_passes > 2:
        raise ValueError("prior campaign contains an impossible pass streak")
    campaign = ContinuousV3TwoRunCampaign(
        execution_identity_sha256,
        runs=recovered,
        consecutive_passes=recovered_consecutive_passes,
        total_provider_calls=total_calls,
        restart_after_last_pass=recovered_consecutive_passes == 1,
        controlled_restart_count=0,
    )
    recovery: dict[str, Any] = {
        "schema_version": "cera.sillytavern_continuous_v3_prior_recovery.v1",
        "prior_campaign_id": HISTORICAL_CAMPAIGN_ID,
        "prior_campaign_result_sha256": bytes_sha256(campaign_path.read_bytes()),
        "prior_execution_manifest_sha256": bytes_sha256(manifest_path.read_bytes()),
        "prior_recovery_sha256": prior_recovery_sha256,
        "new_execution_identity_sha256": execution_identity_sha256,
        "execution_identity_changed": not same_execution_identity,
        "execution_change_resets_consecutive_passes": not same_execution_identity,
        "recovered_consecutive_passes": recovered_consecutive_passes,
        "restart_boundary_satisfied_by_new_process": (
            recovered_consecutive_passes == 1
        ),
        "recovered_total_provider_calls": total_calls,
        "recovered_codex_family_calls": campaign.codex_family_calls,
        "recovered_deepseek_calls": campaign.deepseek_calls,
        "runs": recovery_runs,
    }
    recovery["recovery_sha256"] = canonical_sha256(recovery)
    return campaign, recovery


def historical_v1_one_call_debit(prior_root: Path) -> dict[str, Any]:
    """Bind immutable Run 001 accounting without making V1 runs reusable."""

    campaign, recovery = recover_prior_campaign(
        prior_root,
        execution_identity_sha256=text_sha256(
            "cera.v2.historical_v1_one_call_debit.validation"
        ),
    )
    if (
        len(campaign.runs) != 1
        or campaign.total_provider_calls != 1
        or campaign.codex_family_calls != 1
        or campaign.deepseek_calls != 0
        or campaign.runs[0].run_id != CONTINUOUS_V3_RUN_IDENTITIES[0]
        or campaign.runs[0].calls != (CONTINUOUS_V3_CALL_SCHEDULE[0],)
        or len(recovery.get("runs", ())) != 1
    ):
        raise ValueError("historical V1 debit is not exactly immutable Run 001")
    run = recovery["runs"][0]
    payload = {
        "schema_version": "cera.sillytavern_historical_v1_call_debit.v1",
        "campaign_id": HISTORICAL_CAMPAIGN_ID,
        "run_id": CONTINUOUS_V3_RUN_IDENTITIES[0],
        "calls": [CONTINUOUS_V3_CALL_SCHEDULE[0]],
        "codex_family_calls": 1,
        "deepseek_calls": 0,
        "campaign_result_sha256": recovery["prior_campaign_result_sha256"],
        "campaign_execution_manifest_sha256": recovery[
            "prior_execution_manifest_sha256"
        ],
        "run_result_sha256": run["run_result_sha256"],
        "provider_ledger_sha256": run["provider_ledger_sha256"],
        "run_execution_manifest_sha256": run["execution_manifest_sha256"],
    }
    payload["debit_sha256"] = canonical_sha256(payload)
    return payload


def validate_authority(
    cycle: Path,
    *,
    expected_checkpoint_sha: str,
    expected_authorization_sha256: str,
    expected_cycle_id: str,
    expected_cycle_sequence: int,
    expected_task_id: str,
) -> dict[str, Any]:
    manifest = load_v2_manifest(ROOT, cycle)
    if (
        manifest.get("cycle_id") != expected_cycle_id
        or manifest.get("cycle_sequence") != expected_cycle_sequence
        or manifest.get("checkpoint", {}).get("git_sha") != expected_checkpoint_sha
        or manifest.get("job4", {}).get("task_id") != expected_task_id
        or manifest.get("job4", {}).get("authorization_record_sha256")
        != expected_authorization_sha256
    ):
        raise ValueError("continuous SillyTavern campaign authority changed")
    source_authorization = cycle / "source" / "JOB4_AUTHORIZATION.json"
    outbox_authorization = cycle / "outbox" / "JOB4_AUTHORIZATION.json"
    if any(
        not path.is_file()
        or bytes_sha256(path.read_bytes()) != expected_authorization_sha256
        for path in (source_authorization, outbox_authorization)
    ):
        raise ValueError("continuous SillyTavern campaign authorization bytes changed")
    published_sha256, started_sha256 = validate_publication(
        ROOT, cycle, manifest, require_current_source=True
    )
    _, trigger_sha256 = validate_trigger_receipt(cycle, manifest, started_sha256)
    state = validate_state_view(
        cycle,
        manifest,
        "job4_in_progress",
        "TRIGGER_SENT.json",
        trigger_sha256,
    )
    return {
        "manifest": manifest,
        "cycle_manifest_sha256": bytes_sha256(
            (cycle / "CYCLE_MANIFEST.json").read_bytes()
        ),
        "changed_source_manifest_sha256": bytes_sha256(
            (cycle / "outbox" / "CHANGED_SOURCE_MANIFEST.json").read_bytes()
        ),
        "published_receipt_sha256": published_sha256,
        "job4_started_receipt_sha256": started_sha256,
        "trigger_receipt_sha256": trigger_sha256,
        "cycle_state_sha256": bytes_sha256(
            (cycle / "state" / "CYCLE_STATE.json").read_bytes()
        ),
        "cycle_state": state["state"],
        "authorization_record_sha256": expected_authorization_sha256,
    }


def _git_output(*arguments: str) -> bytes:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout


def _tracked_execution_files() -> tuple[dict[str, str], str, str]:
    for arguments in (("diff", "--quiet", "--no-ext-diff", "--"), ("diff", "--cached", "--quiet", "--")):
        completed = subprocess.run(
            ("git", *arguments),
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            raise ValueError("tracked repository bytes changed after checkpoint")
    head = _git_output("rev-parse", "HEAD").decode("ascii").strip()
    tree = _git_output("rev-parse", "HEAD^{tree}").decode("ascii").strip()
    names = tuple(
        value.decode("utf-8")
        for value in _git_output("ls-files", "-z").split(b"\0")
        if value
    )
    files: dict[str, str] = {}
    for relative in names:
        path = ROOT / relative
        if not path.is_file():
            raise ValueError(f"tracked execution file is unavailable: {relative}")
        files[relative.replace("\\", "/")] = bytes_sha256(path.read_bytes())
    return files, head, tree


def execution_manifest(
    source_db: Path,
    *,
    cycle: Path,
    historical_v1_campaign_root: Path,
    transport_mode: str,
    provider_activation_path: Path | None,
    expected_checkpoint_sha: str,
    expected_authorization_sha256: str,
    expected_cycle_id: str,
    expected_cycle_sequence: int,
    expected_task_id: str,
    provider_free_failure_run_id: str | None = None,
    provider_free_failure_fixture_sha256: str | None = None,
) -> dict[str, Any]:
    authority = validate_authority(
        cycle,
        expected_checkpoint_sha=expected_checkpoint_sha,
        expected_authorization_sha256=expected_authorization_sha256,
        expected_cycle_id=expected_cycle_id,
        expected_cycle_sequence=expected_cycle_sequence,
        expected_task_id=expected_task_id,
    )
    manifest = authority["manifest"]
    tracked_files, actual_head, actual_tree = _tracked_execution_files()
    if actual_head != expected_checkpoint_sha:
        raise ValueError("runtime Git HEAD differs from the published checkpoint")
    profile_path = ROOT / "integrations" / "sillytavern" / "continuous_v3_test_profile.json"
    profile = read_json(profile_path)
    expected_profile = {
        "profile_id": PROFILE_ID,
        "production": False,
        "endpoint": f"http://127.0.0.1:{FIXED_PORT}/v1",
        "model": CERA_CONTINUOUS_V3_TEST_MODEL,
        "stream": False,
        "route": "continuous_v3_test_only",
        "automatic_retry": False,
        "fallback": False,
        "automatic_false_positive": False,
        "creator_review_required": True,
        "database_policy": "fresh_disposable_per_run",
        "lan_binding_allowed": False,
    }
    if any(profile.get(key) != value for key, value in expected_profile.items()):
        raise ValueError("continuous SillyTavern execution profile changed")
    provider_routes = {
        "planner": {"model": "gpt-5.6-sol", "effort": "medium", "fast": False},
        "composer": {"model": "deepseek-v4-flash", "thinking": False},
        "validator": {"model": "gpt-5.6-terra", "effort": "high", "fast": False},
    }
    if (
        profile.get("planner")
        != {"model": "gpt-5.6-sol", "reasoning_effort": "medium", "fast_mode": False}
        or profile.get("composer") != provider_routes["composer"]
        or profile.get("validator")
        != {"model": "gpt-5.6-terra", "reasoning_effort": "high", "fast_mode": False}
    ):
        raise ValueError("continuous provider route profile changed")
    if provider_routes != CONTINUOUS_PROVIDER_MODELS:
        raise ValueError("continuous provider authority model set changed")
    if transport_mode == "non_network_fake_ports":
        if provider_activation_path is not None:
            raise ValueError("fake provider ports reject an activation receipt")
        transport_binding = {
            "mode": transport_mode,
            "external_provider_calls_authorized": 0,
            "provider_activation_relative_path": None,
            "provider_activation_file_sha256": None,
            "provider_activation_receipt_sha256": None,
            "fake_fixture_id": SCRIPTED_JOB4_FIXTURE_ID,
            "fake_fixture_sha256": SCRIPTED_JOB4_FIXTURE_SHA256,
            "provider_free_partial_failure": (
                None
                if provider_free_failure_run_id is None
                else {
                    **PROVIDER_FREE_PARTIAL_FAILURE_FIXTURE,
                    "run_id": provider_free_failure_run_id,
                    "fixture_sha256": (
                        provider_free_failure_fixture_sha256
                    ),
                }
            ),
        }
    elif transport_mode == "external_provider":
        if provider_activation_path is None or not provider_activation_path.is_file():
            raise ValueError("external provider mode requires activation authority")
        activation = validate_provider_activation(
            read_json(provider_activation_path),
            expected_cycle_id=expected_cycle_id,
            expected_cycle_sequence=expected_cycle_sequence,
            expected_job4_task_id=expected_task_id,
            expected_job4_authorization_sha256=expected_authorization_sha256,
            expected_route_profile_id=PROFILE_ID,
            maximum_codex_family_calls=(
                CONTINUOUS_V3_V2_CODEX_FAMILY_CALL_CEILING
            ),
            maximum_deepseek_calls=CONTINUOUS_V3_V2_DEEPSEEK_CALL_CEILING,
        )
        if activation["authority_source_sha256"] != authority[
            "cycle_manifest_sha256"
        ]:
            raise ValueError("provider activation source is not the bound cycle")
        transport_binding = {
            "mode": transport_mode,
            "external_provider_calls_authorized": (
                activation["maximum_codex_family_calls"]
                + activation["maximum_deepseek_calls"]
            ),
            "provider_activation_relative_path": str(
                provider_activation_path.resolve()
            ),
            "provider_activation_file_sha256": bytes_sha256(
                provider_activation_path.read_bytes()
            ),
            "provider_activation_receipt_sha256": activation[
                "activation_sha256"
            ],
            "fake_fixture_id": None,
            "fake_fixture_sha256": None,
            "provider_free_partial_failure": None,
        }
    else:
        raise ValueError("unknown continuous campaign transport mode")

    cycle_authority = {
        "cycle_id": manifest["cycle_id"],
        "cycle_sequence": manifest["cycle_sequence"],
        "checkpoint_git_sha": expected_checkpoint_sha,
        "cycle_manifest_sha256": authority["cycle_manifest_sha256"],
        "manifest_root_sha256": manifest["manifest_root_sha256"],
        "repository_identity_sha256": manifest["repository_identity_sha256"],
        "task_set_sha256": manifest["task_set_sha256"],
        "changed_source_manifest_sha256": authority[
            "changed_source_manifest_sha256"
        ],
        "published_receipt_sha256": authority["published_receipt_sha256"],
        "job4_task_id": manifest["job4"]["task_id"],
        "job4_scope_sha256": manifest["job4"]["scope_sha256"],
        "job4_started_receipt_sha256": authority[
            "job4_started_receipt_sha256"
        ],
        "trigger_receipt_sha256": authority["trigger_receipt_sha256"],
        "authorization_record_sha256": authority[
            "authorization_record_sha256"
        ],
        "cycle_state": authority["cycle_state"],
        "cycle_state_sha256": authority["cycle_state_sha256"],
    }
    route_binding = {
        "profile_id": PROFILE_ID,
        "profile_path": "integrations/sillytavern/continuous_v3_test_profile.json",
        "profile_sha256": bytes_sha256(profile_path.read_bytes()),
        "profile": profile,
        "host": "127.0.0.1",
        "port": FIXED_PORT,
        "model": CERA_CONTINUOUS_V3_TEST_MODEL,
    }
    prompt_bindings = {
        "planner_prompt_version": CONTINUOUS_PLANNER_PROMPT_VERSION,
        "planner_stable_instructions_sha256": text_sha256(
            PLANNER_STABLE_INSTRUCTIONS
        ),
        "planner_base_instructions_sha256": text_sha256(
            _BASE_INSTRUCTIONS_BY_ROLE["scene_reasoner"]
        ),
        "composer_prompt_version": CONTINUOUS_DEEPSEEK_PROMPT_VERSION,
        "validator_prompt_version": CONTINUOUS_VALIDATOR_PROMPT_VERSION,
        "validator_stable_instructions_sha256": text_sha256(
            VALIDATOR_STABLE_INSTRUCTIONS
        ),
        "validator_base_instructions_sha256": text_sha256(
            _BASE_INSTRUCTIONS_BY_ROLE["scene_realization_verifier"]
        ),
    }
    schema_bindings = {
        "planner_sequence": RichPlannerSequenceV1.SCHEMA_VERSION,
        "validator_package": ValidatorFinalizationPackageV1.SCHEMA_VERSION,
        "http_review": "cera.sillytavern_continuous_v3_review.v2",
        "campaign_configuration": CONTINUOUS_V3_CAMPAIGN_CONFIGURATION_V2_SCHEMA,
    }
    policy_bindings = {
        "persistence_policy_sha256": PERSISTENCE_POLICY_SHA256,
        "accepted_snapshot_path_policy_sha256": (
            CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_SHA256
        ),
        "strict_accept_only": True,
        "automatic_retry": False,
        "fallback": False,
        "automatic_false_positive": False,
    }
    fixture_binding = {
        "messages": list(CONTINUOUS_V3_TEST_FIXTURE),
        "fixture_sha256": canonical_sha256(CONTINUOUS_V3_TEST_FIXTURE),
        "call_schedule": list(CONTINUOUS_V3_CALL_SCHEDULE),
        "call_schedule_sha256": canonical_sha256(CONTINUOUS_V3_CALL_SCHEDULE),
    }
    source_bindings = {
        "checkpoint_sha": expected_checkpoint_sha,
        "actual_head_sha": actual_head,
        "actual_tree_sha": actual_tree,
        "tracked_file_count": len(tracked_files),
        "tracked_files_root_sha256": canonical_sha256(tracked_files),
        "tracked_files": tracked_files,
        "source_database_sha256": bytes_sha256(source_db.read_bytes()),
    }
    configuration: dict[str, Any] = {
        "schema_version": CONTINUOUS_V3_CAMPAIGN_CONFIGURATION_V2_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "cycle_authority": cycle_authority,
        "run_identities": list(CONTINUOUS_V3_V2_RUN_IDENTITIES),
        "call_budget": {
            "creator_codex_family_total": 800,
            "creator_deepseek_total": 800,
            "prior_codex_family_debit": 1,
            "prior_deepseek_debit": 0,
            "remaining_codex_family_calls": (
                CONTINUOUS_V3_V2_CODEX_FAMILY_CALL_CEILING
            ),
            "remaining_deepseek_calls": (
                CONTINUOUS_V3_V2_DEEPSEEK_CALL_CEILING
            ),
            "campaign_total_stage_ceiling": (
                CONTINUOUS_V3_V2_CAMPAIGN_TOTAL_CALL_CEILING
            ),
            "per_run_stage_ceiling": len(CONTINUOUS_V3_CALL_SCHEDULE),
        },
        "historical_v1_debit": historical_v1_one_call_debit(
            historical_v1_campaign_root
        ),
        "fixture": fixture_binding,
        "route": route_binding,
        "providers": provider_routes,
        "prompt_bindings": prompt_bindings,
        "schema_bindings": schema_bindings,
        "policy_bindings": policy_bindings,
        "source_bindings": source_bindings,
        "transport_mode": transport_binding,
    }
    configuration["configuration_sha256"] = canonical_sha256(configuration)
    configuration = validate_v2_campaign_configuration(configuration)
    payload: dict[str, Any] = {
        "schema_version": "cera.sillytavern_continuous_v3_execution_manifest.v3",
        "campaign_configuration_sha256": configuration[
            "configuration_sha256"
        ],
        "campaign_configuration": configuration,
    }
    payload["execution_identity_sha256"] = canonical_sha256(payload)
    return payload


def assert_exact_execution_manifest(
    supplied: dict[str, Any],
    recomputed: dict[str, Any],
    *,
    expected_identity_sha256: str,
) -> None:
    for label, value in (("supplied", supplied), ("recomputed", recomputed)):
        identity = value.get("execution_identity_sha256")
        unsigned = {
            key: item
            for key, item in value.items()
            if key != "execution_identity_sha256"
        }
        if (
            not isinstance(identity, str)
            or not re_is_sha256(identity)
            or canonical_sha256(unsigned) != identity
        ):
            raise ValueError(f"{label} execution manifest is not self-bound")
    if supplied["execution_identity_sha256"] != expected_identity_sha256:
        raise ValueError("command execution identity disagrees with supplied manifest")
    if canonical_bytes(supplied) != canonical_bytes(recomputed):
        raise ValueError("child recomputed execution or authority identity changed")


def _manifest_configuration(manifest: dict[str, Any]) -> dict[str, Any]:
    if set(manifest) != {
        "schema_version",
        "campaign_configuration_sha256",
        "campaign_configuration",
        "execution_identity_sha256",
    } or manifest.get("schema_version") != (
        "cera.sillytavern_continuous_v3_execution_manifest.v3"
    ):
        raise ValueError("V2 campaign execution manifest fields changed")
    configuration = validate_v2_campaign_configuration(
        manifest.get("campaign_configuration")
    )
    if manifest.get("campaign_configuration_sha256") != configuration[
        "configuration_sha256"
    ]:
        raise ValueError("execution manifest campaign configuration changed")
    return configuration


def _new_v2_campaign(execution_identity_sha256: str) -> ContinuousV3TwoRunCampaign:
    return ContinuousV3TwoRunCampaign(
        execution_identity_sha256,
        run_identities=CONTINUOUS_V3_V2_RUN_IDENTITIES,
        total_call_ceiling=CONTINUOUS_V3_V2_CAMPAIGN_TOTAL_CALL_CEILING,
        codex_family_call_ceiling=CONTINUOUS_V3_V2_CODEX_FAMILY_CALL_CEILING,
        deepseek_call_ceiling=CONTINUOUS_V3_V2_DEEPSEEK_CALL_CEILING,
    )


def _v2_campaign_from_dict(
    value: dict[str, Any], *, execution_identity_sha256: str
) -> ContinuousV3TwoRunCampaign:
    supplied_sha256 = value.get("campaign_state_sha256")
    unsigned = dict(value)
    unsigned.pop("campaign_state_sha256", None)
    if supplied_sha256 != canonical_sha256(unsigned):
        raise ValueError("V2 campaign state hash changed")
    if value.get("execution_identity_sha256") != execution_identity_sha256:
        raise ValueError("V2 campaign execution identity changed")
    if tuple(value.get("run_identities", ())) != CONTINUOUS_V3_V2_RUN_IDENTITIES:
        raise ValueError("V2 campaign state reused a historical run identity")
    if (
        value.get("total_call_ceiling")
        != CONTINUOUS_V3_V2_CAMPAIGN_TOTAL_CALL_CEILING
        or value.get("codex_family_call_ceiling")
        != CONTINUOUS_V3_V2_CODEX_FAMILY_CALL_CEILING
        or value.get(
            "deepseek_call_ceiling", CONTINUOUS_V3_V2_DEEPSEEK_CALL_CEILING
        )
        != CONTINUOUS_V3_V2_DEEPSEEK_CALL_CEILING
    ):
        raise ValueError("V2 campaign state call ceiling changed")
    raw_runs = value.get("runs")
    if not isinstance(raw_runs, list):
        raise ValueError("V2 campaign run history is malformed")
    runs: list[CampaignRunRecord] = []
    for raw in raw_runs:
        if not isinstance(raw, dict):
            raise ValueError("V2 campaign run history is malformed")
        try:
            state = CampaignRunState(raw["state"])
        except (KeyError, ValueError) as exc:
            raise ValueError("V2 campaign run state is invalid") from exc
        calls = tuple(raw.get("calls", ()))
        runs.append(
            CampaignRunRecord(
                run_id=str(raw.get("run_id", "")),
                state=state,
                execution_identity_sha256=str(
                    raw.get("execution_identity_sha256", "")
                ),
                calls=calls,
                terminal_reason=raw.get("terminal_reason"),
            )
        )
    campaign = ContinuousV3TwoRunCampaign(
        execution_identity_sha256,
        runs=runs,
        consecutive_passes=value.get("consecutive_passes"),
        total_provider_calls=value.get("total_provider_calls"),
        restart_after_last_pass=value.get("restart_after_last_pass"),
        controlled_restart_count=value.get("controlled_restart_count"),
        run_identities=CONTINUOUS_V3_V2_RUN_IDENTITIES,
        total_call_ceiling=CONTINUOUS_V3_V2_CAMPAIGN_TOTAL_CALL_CEILING,
        codex_family_call_ceiling=CONTINUOUS_V3_V2_CODEX_FAMILY_CALL_CEILING,
        deepseek_call_ceiling=CONTINUOUS_V3_V2_DEEPSEEK_CALL_CEILING,
    )
    if canonical_bytes(campaign.to_dict()) != canonical_bytes(value):
        raise ValueError("V2 campaign state is not canonical")
    return campaign


def _campaign_state_artifact(
    configuration: dict[str, Any], campaign: ContinuousV3TwoRunCampaign
) -> dict[str, Any]:
    payload = {
        "schema_version": "cera.sillytavern_continuous_v3_campaign_state.v2",
        "campaign_configuration_sha256": configuration["configuration_sha256"],
        "campaign": campaign.to_dict(),
    }
    return {**payload, "state_artifact_sha256": canonical_sha256(payload)}


def recover_v2_campaign(
    prior_root: Path, *, expected_manifest: dict[str, Any]
) -> tuple[ContinuousV3TwoRunCampaign, dict[str, Any]]:
    """Recover only immutable V2 evidence with the exact current authority."""

    root = prior_root.resolve()
    configuration = _manifest_configuration(expected_manifest)
    configuration_path = root / "CAMPAIGN_CONFIGURATION.json"
    result_path = root / "CAMPAIGN_RESULT.json"
    if not configuration_path.is_file() or not result_path.is_file():
        raise ValueError("prior V2 campaign evidence is incomplete")
    stored_configuration = validate_v2_campaign_configuration(
        read_json(configuration_path)
    )
    if canonical_bytes(stored_configuration) != canonical_bytes(configuration):
        raise ValueError("prior V2 campaign authority differs from current authority")
    result = read_json(result_path)
    supplied_result_sha256 = result.get("result_sha256")
    unsigned_result = dict(result)
    unsigned_result.pop("result_sha256", None)
    if (
        result.get("schema_version")
        != "cera.sillytavern_continuous_v3_campaign_result.v3"
        or result.get("campaign_id") != CAMPAIGN_ID
        or supplied_result_sha256 != canonical_sha256(unsigned_result)
        or result.get("campaign_configuration_sha256")
        != configuration["configuration_sha256"]
        or result.get("campaign_configuration_file_sha256")
        != bytes_sha256(configuration_path.read_bytes())
        or result.get("execution_identity_sha256")
        != expected_manifest["execution_identity_sha256"]
    ):
        raise ValueError("prior V2 campaign result authority changed")
    campaign = _v2_campaign_from_dict(
        result.get("campaign", {}),
        execution_identity_sha256=expected_manifest["execution_identity_sha256"],
    )
    bindings = result.get("run_reconciliations")
    if (
        not isinstance(bindings, list)
        or result.get("run_reconciliations_sha256")
        != canonical_sha256(bindings)
        or len(bindings) != len(campaign.runs)
    ):
        raise ValueError("prior V2 reconciliation index changed")
    recovery_runs: list[dict[str, Any]] = []
    for record, binding in zip(campaign.runs, bindings, strict=True):
        if not isinstance(binding, dict) or binding.get("run_id") != record.run_id:
            raise ValueError("prior V2 reconciliation binding changed")
        evidence_root = Path(str(binding.get("evidence_root", ""))).resolve()
        reconciliation_path = evidence_root / str(
            binding.get("relative_path", "")
        )
        expected_path = (
            evidence_root
            / "runs"
            / record.run_id
            / "PARENT_RUN_RECONCILIATION.json"
        )
        if reconciliation_path.resolve() != expected_path.resolve():
            raise ValueError("prior V2 reconciliation path changed")
        reconciliation = read_json(reconciliation_path)
        _validate_child_reconciliation(
            reconciliation,
            run_root=expected_path.parent,
            expected_run_id=record.run_id,
        )
        if (
            reconciliation.get("campaign_configuration_sha256")
            != configuration["configuration_sha256"]
            or tuple(reconciliation.get("calls", ())) != record.calls
            or reconciliation.get("passed")
            is not (record.state is CampaignRunState.PASSED)
            or binding.get("file_sha256")
            != bytes_sha256(reconciliation_path.read_bytes())
            or binding.get("receipt_sha256")
            != reconciliation["reconciliation_sha256"]
        ):
            raise ValueError("prior V2 run reconciliation changed")
        recovery_runs.append(
            {
                "run_id": record.run_id,
                "state": record.state.value,
                "calls": list(record.calls),
                "evidence_root": str(evidence_root),
                "relative_path": binding["relative_path"],
                "reconciliation_file_sha256": binding["file_sha256"],
                "reconciliation_receipt_sha256": binding["receipt_sha256"],
            }
        )
    payload = {
        "schema_version": "cera.sillytavern_continuous_v3_prior_v2_recovery.v1",
        "campaign_id": CAMPAIGN_ID,
        "campaign_configuration_sha256": configuration["configuration_sha256"],
        "prior_campaign_result_sha256": bytes_sha256(result_path.read_bytes()),
        "prior_campaign_result_receipt_sha256": supplied_result_sha256,
        "execution_identity_sha256": expected_manifest["execution_identity_sha256"],
        "runs": recovery_runs,
        "total_stage_invocations": campaign.total_provider_calls,
        "codex_family_stage_invocations": campaign.codex_family_calls,
        "deepseek_stage_invocations": campaign.deepseek_calls,
    }
    return campaign, {**payload, "recovery_sha256": canonical_sha256(payload)}


def _record_primary_or_additive_failure(
    result: dict[str, Any],
    error: BaseException,
    *,
    stage: str,
) -> None:
    """Keep the first failure authoritative and make cleanup failures additive."""

    result["status"] = "failed"
    if "error_type" not in result:
        result["error_type"] = type(error).__name__
        result["error_message"] = str(error)
        result["failure_stage"] = stage
        return
    result[f"{stage}_error_type"] = type(error).__name__
    result[f"{stage}_error_message"] = str(error)


def http_json(base: str, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=1800) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


def sqlite_checks(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return {
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_findings": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
        }
    finally:
        connection.close()


def _build_campaign_harness(
    *,
    transport_mode: str,
    run_id: str,
    cycle: Path,
    world: ContinuousWorldStore,
    lifecycle_root: Path,
    call_ledger: ContinuousProviderCallLedger,
    lineage: ContinuousThreadLineageLedger,
) -> tuple[
    ExitStack,
    ContinuousSessionCoordinator,
    ContinuousSessionCoordinator,
    JobHarness,
]:
    """Construct the exact provider route, with fake ports remaining offline."""

    provider_stack = ExitStack()
    if transport_mode == "non_network_fake_ports":
        session_port = InMemoryContinuousStoredSessionPort()
        planner_session = ContinuousSessionCoordinator(
            compatibility(world, ContinuousSessionRole.PLANNER), session_port
        )
        validator_session = ContinuousSessionCoordinator(
            compatibility(world, ContinuousSessionRole.VALIDATOR), session_port
        )
        planner_session.install_base_instructions(PLANNER_STABLE_INSTRUCTIONS)
        validator_session.install_base_instructions(VALIDATOR_STABLE_INSTRUCTIONS)
        fixture = ScriptedJob4FixtureRuntime(world_id=WORLD_ID, branch_id=BRANCH_ID)
        factories = {
            "planner_transport_factory": fixture.planner_transport,
            "validator_transport_factory": fixture.validator_transport,
            "composer_transport_factory": fixture.composer_transport,
            "scripted_provider_free": True,
        }
    elif transport_mode == "external_provider":
        assert_provider_dispatch_allowed(
            "scripts.continuous_v3_campaign.provider_runtime"
        )
        from openai_codex import Codex, CodexConfig

        codex = provider_stack.enter_context(
            Codex(CodexConfig(config_overrides=("mcp_servers={}",), env={}))
        )
        account = codex.account()
        if account.account is None:
            raise RuntimeError("ChatGPT Codex session is unavailable")
        planner_backend = OpenAICodexStoredThreadBackend(
            codex=codex,
            model="gpt-5.6-sol",
            cwd=str(lifecycle_root),
            base_instructions=(
                _BASE_INSTRUCTIONS_BY_ROLE["scene_reasoner"]
                + "\n\n"
                + PLANNER_STABLE_INSTRUCTIONS
            ),
            service_name=f"cera_st_v3_planner_{run_id[-3:]}",
        )
        validator_backend = OpenAICodexStoredThreadBackend(
            codex=codex,
            model="gpt-5.6-terra",
            cwd=str(lifecycle_root),
            base_instructions=(
                _BASE_INSTRUCTIONS_BY_ROLE["scene_realization_verifier"]
                + "\n\n"
                + VALIDATOR_STABLE_INSTRUCTIONS
            ),
            service_name=f"cera_st_v3_validator_{run_id[-3:]}",
        )
        planner_session = ContinuousSessionCoordinator(
            compatibility(world, ContinuousSessionRole.PLANNER),
            CodexContinuousStoredSessionPort(planner_backend),
            base_instructions=planner_backend.base_instructions,
        )
        validator_session = ContinuousSessionCoordinator(
            compatibility(world, ContinuousSessionRole.VALIDATOR),
            CodexContinuousStoredSessionPort(validator_backend),
            base_instructions=validator_backend.base_instructions,
        )
        factories = {}
    else:
        provider_stack.close()
        raise ValueError("unknown campaign transport mode")

    planner_session.attach_thread_lineage(lineage, purpose="primary_planner")
    validator_session.attach_thread_lineage(lineage, purpose="primary_validator")
    planner_handle = planner_session.ensure_session().provider_thread_id
    validator_handle = validator_session.ensure_session().provider_thread_id
    assert_separate_role_sessions(planner_session, validator_session)
    harness = JobHarness(
        source_root=ROOT,
        cycle=cycle,
        world=world,
        planner_session=planner_session,
        validator_session=validator_session,
        planner_handle=planner_handle,
        validator_handle=validator_handle,
        lifecycle_root=lifecycle_root,
        call_ledger=call_ledger,
        **factories,
    )
    if transport_mode == "non_network_fake_ports":
        fixture.bind(harness)
    return provider_stack, planner_session, validator_session, harness


def run_single(args: argparse.Namespace) -> int:
    cycle = args.cycle_directory.resolve()
    source_db = args.source_database.resolve()
    run_root = args.runtime_root.resolve()
    if args.run_id not in CONTINUOUS_V3_V2_RUN_IDENTITIES:
        raise ValueError("child accepts only a fresh V2 campaign run identity")
    supplied_manifest = read_json(args.execution_manifest)
    recomputed_manifest = execution_manifest(
        source_db,
        cycle=cycle,
        historical_v1_campaign_root=args.historical_v1_campaign_root.resolve(),
        transport_mode=args.transport_mode,
        provider_activation_path=(
            None
            if args.provider_activation is None
            else args.provider_activation.resolve()
        ),
        expected_checkpoint_sha=args.expected_checkpoint_sha,
        expected_authorization_sha256=args.expected_authorization_sha256,
        expected_cycle_id=args.expected_cycle_id,
        expected_cycle_sequence=args.expected_cycle_sequence,
        expected_task_id=args.expected_job4_task_id,
        provider_free_failure_run_id=getattr(
            args, "provider_free_failure_run_id", None
        ),
        provider_free_failure_fixture_sha256=(
            getattr(
                args,
                "confirm_provider_free_partial_failure_fixture_sha256",
                None,
            )
        ),
    )
    assert_exact_execution_manifest(
        supplied_manifest,
        recomputed_manifest,
        expected_identity_sha256=args.execution_identity_sha256,
    )
    configuration = _manifest_configuration(recomputed_manifest)
    if (
        args.run_id not in configuration["run_identities"]
        or configuration["transport_mode"]["mode"] != args.transport_mode
    ):
        raise ValueError("child command differs from its campaign configuration")
    if run_root.exists():
        raise FileExistsError("refusing to overwrite immutable run evidence")
    assert_provider_dispatch_allowed(
        "scripts.continuous_v3_campaign.single_run",
        external_provider_boundary=args.transport_mode == "external_provider",
    )
    run_root.mkdir(parents=True)
    result_path = run_root / "RUN_RESULT.json"
    copied_db = run_root / "hanezawa_disposable.sqlite3"
    shutil.copy2(source_db, copied_db)
    source_before = bytes_sha256(source_db.read_bytes())
    copy_before = bytes_sha256(copied_db.read_bytes())
    checks_before = sqlite_checks(copied_db)
    world = ContinuousWorldStore(run_root / "worlds")
    seed_world(world, ROOT)
    lifecycle_root = run_root / "provider_workspaces"
    lifecycle_root.mkdir()
    call_ledger = ContinuousProviderCallLedger(
        run_root / "PROVIDER_CALL_LEDGER.jsonl", maximum_calls=10
    )
    result: dict[str, Any] = {
        "schema_version": "cera.sillytavern_continuous_v3_run_result.v2",
        "run_id": args.run_id,
        "campaign_configuration_sha256": configuration[
            "configuration_sha256"
        ],
        "transport_mode": args.transport_mode,
        "status": "running",
        "source_database_sha256_before": source_before,
        "disposable_database_sha256_before": copy_before,
        "sqlite_before": checks_before,
        "http": [],
        "transport_lifecycle": {
            "provider_context_opened": False,
            "thread_terminalization_attempted": False,
            "thread_terminalization_completed_while_transport_alive": False,
            "provider_context_close_started_after_terminalization_attempt": False,
            "provider_context_closed": False,
        },
    }
    write_json(run_root / "EXECUTION_MANIFEST.json", recomputed_manifest)
    write_json(run_root / "CAMPAIGN_CONFIGURATION.json", configuration)
    planner_session = validator_session = None
    lineage = ContinuousThreadLineageLedger()
    harness = None
    server = None
    server_thread = None
    provider_stack: ExitStack | None = None
    try:
        (
            provider_stack,
            planner_session,
            validator_session,
            harness,
        ) = _build_campaign_harness(
            transport_mode=args.transport_mode,
            run_id=args.run_id,
            cycle=cycle,
            world=world,
            lifecycle_root=lifecycle_root,
            call_ledger=call_ledger,
            lineage=lineage,
        )
        result["transport_lifecycle"]["provider_context_opened"] = True
        if provider_stack is not None:
            adapter = ContinuousSillyTavernTestAdapter(
                session_id=args.run_id,
                prepare_turn=harness.prepare_http_turn,
                accept_turn=harness.accept_http_turn,
                route_identity={
                    "profile_id": PROFILE_ID,
                    "run_id": args.run_id,
                    "execution_identity_sha256": args.execution_identity_sha256,
                },
            )
            server = build_server(
                adapter,
                CeraSillyTavernServerConfig(
                    host="127.0.0.1",
                    port=FIXED_PORT,
                    model=CERA_CONTINUOUS_V3_TEST_MODEL,
                    service="cera-sillytavern-continuous-v3-test",
                ),
            )
            server_thread = Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            base = f"http://127.0.0.1:{FIXED_PORT}"
            for path in ("/health", "/v1/models"):
                status, body = http_json(base, "GET", path)
                result["http"].append({"method": "GET", "path": path, "status": status, "body": body})
                if status != 200:
                    raise RuntimeError(f"campaign HTTP preflight failed: {path}")
            for turn_number, message in enumerate(CONTINUOUS_V3_TEST_FIXTURE, 1):
                status, reply = http_json(
                    base,
                    "POST",
                    "/v1/chat/completions",
                    {
                        "model": CERA_CONTINUOUS_V3_TEST_MODEL,
                        "stream": False,
                        "cera_session_id": args.run_id,
                        "cera_scene_change": turn_number == 3,
                        "messages": [{"role": "user", "content": message}],
                    },
                )
                result["http"].append({"method": "POST", "path": "/v1/chat/completions", "turn": turn_number, "status": status, "body": reply})
                if status != 200:
                    raise RuntimeError(f"turn {turn_number} HTTP completion failed")
                review_id = reply["cera"]["provisional_review_id"]
                status, review = http_json(base, "GET", f"/v1/cera/reviews/{review_id}")
                result["http"].append({"method": "GET", "path": f"/v1/cera/reviews/{review_id}", "turn": turn_number, "status": status, "body": review})
                if status != 200 or review.get("state") != "review_ready" or review.get("accept_enabled") is not True:
                    raise RuntimeError(f"turn {turn_number} review is not strictly accept eligible")
                status, decision = http_json(
                    base,
                    "POST",
                    f"/v1/cera/reviews/{review_id}/decision",
                    {"action": CreatorReviewAction.ACCEPT.value},
                )
                result["http"].append({"method": "POST", "path": f"/v1/cera/reviews/{review_id}/decision", "turn": turn_number, "status": status, "body": decision})
                if status != 200 or decision.get("status") != "accepted":
                    raise RuntimeError(f"turn {turn_number} strict acceptance failed")
                if (
                    turn_number == 1
                    and args.run_id
                    == getattr(args, "provider_free_failure_run_id", None)
                ):
                    raise RuntimeError(
                        "provider-free qualification injected failure after "
                        "the exact first-turn three-stage dispatch"
                    )
            labels = tuple(record["label"] for record in harness.call_records)
            expected_external_calls = (
                0 if args.transport_mode == "non_network_fake_ports" else 10
            )
            if (
                labels != CONTINUOUS_V3_CALL_SCHEDULE
                or call_ledger.dispatched_call_count != 10
                or harness.provider_calls != expected_external_calls
            ):
                raise RuntimeError("campaign run did not use the exact ten-call schedule")
            if tuple(value["planner_packet_kind"] for value in harness.http_turn_results) != (
                "first_turn_initialization",
                "lean_continuous_continuation",
                "scene_change",
            ):
                raise RuntimeError("campaign V3 packet progression changed")
            if harness.planner_session.unsynchronized_accepted_turn_ids:
                raise RuntimeError("campaign accepted Planner context is unsynchronized")
            result["turns"] = harness.http_turn_results
            result["calls"] = harness.call_records
            result["scene_summary"] = {
                "present": harness.scene_change_envelope is not None,
                "accepted_pair_count": len(harness.accepted_pairs[:2]),
                "turn_3_excluded": (
                    harness.scene_change_envelope is not None
                    and CONTINUOUS_V3_TEST_FIXTURE[2]
                    not in harness.scene_change_envelope.previous_scene_summary.shortest_complete_summary
                ),
            }
            result["status"] = "passed"
    except BaseException as exc:
        _record_primary_or_additive_failure(result, exc, stage="execution")
    finally:
        if server is not None:
            try:
                server.shutdown()
            except BaseException as exc:
                _record_primary_or_additive_failure(
                    result, exc, stage="http_server_shutdown"
                )
            try:
                server.server_close()
            except BaseException as exc:
                _record_primary_or_additive_failure(
                    result, exc, stage="http_server_close"
                )
        if server_thread is not None:
            try:
                server_thread.join(timeout=5)
                if server_thread.is_alive():
                    raise StateConflictError(
                        "SillyTavern campaign HTTP thread did not stop"
                    )
            except BaseException as exc:
                _record_primary_or_additive_failure(
                    result, exc, stage="http_server_thread_join"
                )
        if planner_session is not None or validator_session is not None:
            result["transport_lifecycle"]["thread_terminalization_attempted"] = True
            try:
                archived, evidence, lineage_receipt = _terminalize_known_thread_sessions(
                    thread_lineage=lineage,
                    planner_session=planner_session,
                    validator_session=validator_session,
                    reason=("sillytavern_v3_run_complete" if result["status"] == "passed" else "sillytavern_v3_run_failed"),
                )
                result["thread_archival"] = archived
                result["thread_archival_evidence"] = evidence
                result["thread_lineage"] = lineage_receipt.to_dict()
                terminalized = (
                    all(archived.values()) and lineage_receipt.status == "verified"
                )
                result["transport_lifecycle"][
                    "thread_terminalization_completed_while_transport_alive"
                ] = terminalized
                if not terminalized:
                    _record_primary_or_additive_failure(
                        result,
                        StateConflictError("physical thread archival failed"),
                        stage="thread_terminalization",
                    )
            except BaseException as exc:
                _record_primary_or_additive_failure(
                    result, exc, stage="thread_terminalization_cleanup"
                )
        if provider_stack is not None:
            result["transport_lifecycle"][
                "provider_context_close_started_after_terminalization_attempt"
            ] = result["transport_lifecycle"]["thread_terminalization_attempted"]
            try:
                provider_stack.close()
                result["transport_lifecycle"]["provider_context_closed"] = True
            except BaseException as exc:
                _record_primary_or_additive_failure(
                    result, exc, stage="provider_context_close"
                )
        if harness is not None:
            result["calls"] = harness.call_records
        result["provider_calls"] = call_ledger.dispatched_call_count
        result["stage_invocations"] = call_ledger.dispatched_call_count
        result["external_provider_calls"] = (
            0
            if args.transport_mode == "non_network_fake_ports"
            else call_ledger.dispatched_call_count
        )
        result["source_database_sha256_after"] = bytes_sha256(source_db.read_bytes())
        result["disposable_database_sha256_after"] = bytes_sha256(copied_db.read_bytes())
        result["sqlite_after"] = sqlite_checks(copied_db)
        if (
            result["source_database_sha256_after"] != source_before
            or result["sqlite_after"] != checks_before
        ):
            result["status"] = "failed"
            result["database_drift"] = True
        write_json(result_path, result)
    return 0 if result["status"] == "passed" else 1


def run_campaign(args: argparse.Namespace) -> int:
    cycle = args.cycle_directory.resolve()
    source_db = args.source_database.resolve()
    campaign_root = args.runtime_root.resolve()
    if campaign_root.exists():
        raise FileExistsError("refusing to overwrite immutable campaign evidence")
    assert_provider_dispatch_allowed(
        "scripts.continuous_v3_campaign.child_process",
        external_provider_boundary=args.transport_mode == "external_provider",
    )
    manifest = execution_manifest(
        source_db,
        cycle=cycle,
        historical_v1_campaign_root=args.historical_v1_campaign_root.resolve(),
        transport_mode=args.transport_mode,
        provider_activation_path=(
            None
            if args.provider_activation is None
            else args.provider_activation.resolve()
        ),
        expected_checkpoint_sha=args.expected_checkpoint_sha,
        expected_authorization_sha256=args.expected_authorization_sha256,
        expected_cycle_id=args.expected_cycle_id,
        expected_cycle_sequence=args.expected_cycle_sequence,
        expected_task_id=args.expected_job4_task_id,
        provider_free_failure_run_id=getattr(
            args, "provider_free_failure_run_id", None
        ),
        provider_free_failure_fixture_sha256=(
            getattr(
                args,
                "confirm_provider_free_partial_failure_fixture_sha256",
                None,
            )
        ),
    )
    campaign_root.mkdir(parents=True)
    manifest_path = campaign_root / "EXECUTION_MANIFEST.json"
    write_json(manifest_path, manifest)
    configuration = _manifest_configuration(manifest)
    configuration_path = campaign_root / "CAMPAIGN_CONFIGURATION.json"
    write_json(configuration_path, configuration)
    execution_identity = manifest["execution_identity_sha256"]
    prior_v2_recovery = None
    if args.prior_v2_campaign_root is None:
        campaign = _new_v2_campaign(execution_identity)
    else:
        campaign, prior_v2_recovery = recover_v2_campaign(
            args.prior_v2_campaign_root,
            expected_manifest=manifest,
        )
        write_json(
            campaign_root / "PRIOR_V2_CAMPAIGN_RECOVERY.json",
            prior_v2_recovery,
        )
    run_reconciliations: list[dict[str, Any]] = (
        []
        if prior_v2_recovery is None
        else [
            {
                "run_id": item["run_id"],
                "evidence_root": item["evidence_root"],
                "relative_path": item["relative_path"],
                "file_sha256": item["reconciliation_file_sha256"],
                "receipt_sha256": item["reconciliation_receipt_sha256"],
            }
            for item in prior_v2_recovery["runs"]
        ]
    )
    for run_id in CONTINUOUS_V3_V2_RUN_IDENTITIES[len(campaign.runs) :]:
        if campaign.complete:
            break
        recomputed_manifest = execution_manifest(
            source_db,
            cycle=cycle,
            historical_v1_campaign_root=(
                args.historical_v1_campaign_root.resolve()
            ),
            transport_mode=args.transport_mode,
            provider_activation_path=(
                None
                if args.provider_activation is None
                else args.provider_activation.resolve()
            ),
            expected_checkpoint_sha=args.expected_checkpoint_sha,
            expected_authorization_sha256=args.expected_authorization_sha256,
            expected_cycle_id=args.expected_cycle_id,
            expected_cycle_sequence=args.expected_cycle_sequence,
            expected_task_id=args.expected_job4_task_id,
            provider_free_failure_run_id=getattr(
                args, "provider_free_failure_run_id", None
            ),
            provider_free_failure_fixture_sha256=(
                getattr(
                    args,
                    "confirm_provider_free_partial_failure_fixture_sha256",
                    None,
                )
            ),
        )
        assert_exact_execution_manifest(
            manifest,
            recomputed_manifest,
            expected_identity_sha256=execution_identity,
        )
        if campaign.consecutive_passes == 1:
            campaign.record_controlled_restart(execution_identity_sha256=execution_identity)
        campaign.begin_run(run_id, execution_identity_sha256=execution_identity)
        run_root = campaign_root / "runs" / run_id
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--single-run",
            "--run-id", run_id,
            "--cycle-directory", str(cycle),
            "--source-database", str(source_db),
            "--runtime-root", str(run_root),
            "--historical-v1-campaign-root",
            str(args.historical_v1_campaign_root.resolve()),
            "--transport-mode",
            args.transport_mode,
            "--expected-checkpoint-sha", args.expected_checkpoint_sha,
            "--expected-cycle-id", args.expected_cycle_id,
            "--expected-cycle-sequence", str(args.expected_cycle_sequence),
            "--expected-job4-task-id", args.expected_job4_task_id,
            "--expected-authorization-sha256", args.expected_authorization_sha256,
            "--execution-manifest", str(manifest_path),
            "--execution-identity-sha256", execution_identity,
        ]
        if args.provider_activation is not None:
            command.extend(
                ["--provider-activation", str(args.provider_activation.resolve())]
            )
        if getattr(args, "provider_free_failure_run_id", None) is not None:
            command.extend(
                [
                    "--provider-free-failure-run-id",
                    args.provider_free_failure_run_id,
                    "--confirm-provider-free-partial-failure-fixture-sha256",
                    args.confirm_provider_free_partial_failure_fixture_sha256,
                ]
            )
        completed = None
        process_error: BaseException | None = None
        try:
            completed = subprocess.run(command, cwd=ROOT, check=False)
        except BaseException as exc:
            process_error = exc
        reconciliation = reconcile_child_run(
            run_root=run_root,
            run_id=run_id,
            execution_identity_sha256=execution_identity,
            process_returncode=(None if completed is None else completed.returncode),
            process_error=process_error,
            campaign_configuration_sha256=configuration[
                "configuration_sha256"
            ],
            transport_mode=args.transport_mode,
        )
        reconciliation_path = run_root / "PARENT_RUN_RECONCILIATION.json"
        run_reconciliations.append(
            {
                "run_id": run_id,
                "evidence_root": str(campaign_root),
                "relative_path": reconciliation_path.relative_to(
                    campaign_root
                ).as_posix(),
                "file_sha256": bytes_sha256(reconciliation_path.read_bytes()),
                "receipt_sha256": reconciliation["reconciliation_sha256"],
            }
        )
        for label in reconciliation["calls"]:
            campaign.record_dispatch(label)
        passed = reconciliation["passed"] is True
        campaign.terminalize(
            passed=passed,
            reason=str(reconciliation["terminal_reason"]),
        )
        write_json(
            campaign_root / "CAMPAIGN_STATE.json",
            _campaign_state_artifact(configuration, campaign),
        )
        if not passed:
            break
    result = {
        "schema_version": "cera.sillytavern_continuous_v3_campaign_result.v3",
        "campaign_id": CAMPAIGN_ID,
        "campaign_configuration_sha256": configuration[
            "configuration_sha256"
        ],
        "campaign_configuration_file_sha256": bytes_sha256(
            configuration_path.read_bytes()
        ),
        "status": "completed_two_consecutive_runs_passed" if campaign.complete else "stopped_without_two_consecutive_passes",
        "execution_manifest_sha256": bytes_sha256(manifest_path.read_bytes()),
        "execution_identity_sha256": execution_identity,
        "campaign": campaign.to_dict(),
        "run_reconciliations": run_reconciliations,
        "run_reconciliations_sha256": canonical_sha256(
            run_reconciliations
        ),
        "historical_v1_debit_sha256": configuration["historical_v1_debit"][
            "debit_sha256"
        ],
        "prior_v2_campaign_recovery_sha256": (
            None
            if prior_v2_recovery is None
            else prior_v2_recovery["recovery_sha256"]
        ),
        "stage_invocations": campaign.total_provider_calls,
        "codex_family_stage_invocations": campaign.codex_family_calls,
        "deepseek_stage_invocations": campaign.deepseek_calls,
        "cumulative_codex_family_calls_with_historical_debit": (
            configuration["historical_v1_debit"]["codex_family_calls"]
            + campaign.codex_family_calls
        ),
        "cumulative_deepseek_calls_with_historical_debit": (
            configuration["historical_v1_debit"]["deepseek_calls"]
            + campaign.deepseek_calls
        ),
        "external_provider_calls": (
            0
            if args.transport_mode == "non_network_fake_ports"
            else campaign.total_provider_calls
        ),
    }
    result["result_sha256"] = canonical_sha256(result)
    write_json(campaign_root / "CAMPAIGN_RESULT.json", result)
    return 0 if campaign.complete else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-v2-campaign", action="store_true")
    parser.add_argument("--single-run", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--cycle-directory", type=Path, required=True)
    parser.add_argument("--source-database", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--expected-checkpoint-sha", required=True)
    parser.add_argument("--expected-cycle-id", required=True)
    parser.add_argument("--expected-cycle-sequence", type=int, required=True)
    parser.add_argument("--expected-job4-task-id", required=True)
    parser.add_argument("--execution-checkpoint-sha")
    parser.add_argument("--expected-authorization-sha256", required=True)
    parser.add_argument("--historical-v1-campaign-root", type=Path, required=True)
    parser.add_argument("--prior-v2-campaign-root", type=Path)
    parser.add_argument(
        "--transport-mode",
        choices=("non_network_fake_ports", "external_provider"),
        required=True,
    )
    parser.add_argument("--provider-activation", type=Path)
    parser.add_argument(
        "--provider-free-failure-run-id",
        choices=CONTINUOUS_V3_V2_RUN_IDENTITIES,
    )
    parser.add_argument(
        "--confirm-provider-free-partial-failure-fixture-sha256"
    )
    parser.add_argument("--execution-manifest", type=Path)
    parser.add_argument("--execution-identity-sha256")
    args = parser.parse_args()
    if (args.provider_free_failure_run_id is None) != (
        args.confirm_provider_free_partial_failure_fixture_sha256 is None
    ):
        parser.error(
            "provider-free partial failure requires both the run identity and "
            "the exact fixture hash"
        )
    if args.provider_free_failure_run_id is not None:
        if args.transport_mode != "non_network_fake_ports":
            parser.error("provider-free partial failure is forbidden in external mode")
        if (
            args.confirm_provider_free_partial_failure_fixture_sha256
            != PROVIDER_FREE_PARTIAL_FAILURE_FIXTURE_SHA256
        ):
            parser.error("provider-free partial failure fixture identity changed")
    if (
        args.execution_checkpoint_sha is not None
        and args.execution_checkpoint_sha != args.expected_checkpoint_sha
    ):
        parser.error("execution checkpoint must equal the published checkpoint")
    if args.single_run:
        if not all((args.run_id, args.execution_manifest, args.execution_identity_sha256)):
            parser.error("single-run mode requires exact run and execution identities")
        return run_single(args)
    if not args.confirm_v2_campaign:
        parser.error("V2 campaign requires --confirm-v2-campaign")
    return run_campaign(args)


if __name__ == "__main__":
    raise SystemExit(main())
