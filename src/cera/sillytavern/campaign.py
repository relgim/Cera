"""Immutable accounting for the bounded Continuous V3 SillyTavern campaign."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, re_is_sha256
from cera.continuous.sessions import (
    CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_SHA256,
)


CONTINUOUS_V3_V1_CAMPAIGN_ID = "2026-08-02-continuous-sillytavern-two-run-v1"
CONTINUOUS_V3_V2_CAMPAIGN_ID = "2026-08-02-continuous-sillytavern-two-run-v2"
CONTINUOUS_V3_RUN_IDENTITIES = tuple(
    f"2026-08-02-continuous-sillytavern-two-run-v1-run-{number:03d}"
    for number in range(1, 5)
)
CONTINUOUS_V3_V2_RUN_IDENTITIES = tuple(
    f"2026-08-02-continuous-sillytavern-two-run-v2-run-{number:03d}"
    for number in range(1, 5)
)
CONTINUOUS_V3_CALL_SCHEDULE = (
    "turn-1-planner",
    "turn-1-deepseek",
    "turn-1-validator",
    "turn-2-planner",
    "turn-2-deepseek",
    "turn-2-validator",
    "scene-1-validator-summary",
    "turn-3-planner",
    "turn-3-deepseek",
    "turn-3-validator",
)

PROVIDER_FREE_PARTIAL_FAILURE_FIXTURE = {
    "schema_version": "cera.sillytavern_continuous_v3_partial_failure_fixture.v1",
    "transport_mode": "non_network_fake_ports",
    "failure_boundary": "after_first_turn_three_stage_dispatch_before_turn_2",
    "stage_invocations_before_failure": 3,
    "external_provider_calls": 0,
    "retry_permitted": False,
}
PROVIDER_FREE_PARTIAL_FAILURE_FIXTURE_SHA256 = canonical_sha256(
    PROVIDER_FREE_PARTIAL_FAILURE_FIXTURE
)

CAMPAIGN_TOTAL_CALL_CEILING = 40
CODEX_FAMILY_CALL_CEILING = 800
DEEPSEEK_CALL_CEILING = 800
CONTINUOUS_V3_V2_CAMPAIGN_TOTAL_CALL_CEILING = 39
CONTINUOUS_V3_V2_CODEX_FAMILY_CALL_CEILING = 799
CONTINUOUS_V3_V2_DEEPSEEK_CALL_CEILING = 800
CONTINUOUS_V3_V2_PRIOR_CODEX_FAMILY_DEBIT = 1
CONTINUOUS_V3_V2_PRIOR_DEEPSEEK_DEBIT = 0

CONTINUOUS_V3_CAMPAIGN_CONFIGURATION_V2_SCHEMA = (
    "cera.sillytavern_continuous_v3_campaign_configuration.v2"
)


def validate_v2_campaign_configuration(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the one parent/child V2 authority configuration.

    Expanded bindings stay in this document rather than being reconstructed
    from abbreviated path or command-line identities.  Every consuming
    artifact records its complete configuration SHA-256.
    """

    if not isinstance(value, Mapping):
        raise ContractValidationError("V2 campaign configuration must be an object")
    data = dict(value)
    expected_fields = {
        "schema_version",
        "campaign_id",
        "cycle_authority",
        "run_identities",
        "call_budget",
        "historical_v1_debit",
        "fixture",
        "route",
        "providers",
        "prompt_bindings",
        "schema_bindings",
        "policy_bindings",
        "source_bindings",
        "transport_mode",
        "configuration_sha256",
    }
    if set(data) != expected_fields:
        raise ContractValidationError("V2 campaign configuration fields changed")
    if (
        data.get("schema_version")
        != CONTINUOUS_V3_CAMPAIGN_CONFIGURATION_V2_SCHEMA
        or data.get("campaign_id") != CONTINUOUS_V3_V2_CAMPAIGN_ID
    ):
        raise ContractValidationError("V2 campaign configuration identity changed")
    if tuple(data.get("run_identities", ())) != CONTINUOUS_V3_V2_RUN_IDENTITIES:
        raise ContractValidationError("V2 campaign run identities changed")
    if set(CONTINUOUS_V3_RUN_IDENTITIES) & set(data["run_identities"]):
        raise ContractValidationError("historical V1 run identity was reused")

    call_budget = data.get("call_budget")
    expected_budget = {
        "creator_codex_family_total": CODEX_FAMILY_CALL_CEILING,
        "creator_deepseek_total": DEEPSEEK_CALL_CEILING,
        "prior_codex_family_debit": (
            CONTINUOUS_V3_V2_PRIOR_CODEX_FAMILY_DEBIT
        ),
        "prior_deepseek_debit": CONTINUOUS_V3_V2_PRIOR_DEEPSEEK_DEBIT,
        "remaining_codex_family_calls": (
            CONTINUOUS_V3_V2_CODEX_FAMILY_CALL_CEILING
        ),
        "remaining_deepseek_calls": CONTINUOUS_V3_V2_DEEPSEEK_CALL_CEILING,
        "campaign_total_stage_ceiling": (
            CONTINUOUS_V3_V2_CAMPAIGN_TOTAL_CALL_CEILING
        ),
        "per_run_stage_ceiling": len(CONTINUOUS_V3_CALL_SCHEDULE),
    }
    if call_budget != expected_budget:
        raise ContractValidationError("V2 campaign call budget changed")

    debit = data.get("historical_v1_debit")
    if (
        not isinstance(debit, Mapping)
        or debit.get("campaign_id") != CONTINUOUS_V3_V1_CAMPAIGN_ID
        or debit.get("run_id") != CONTINUOUS_V3_RUN_IDENTITIES[0]
        or debit.get("codex_family_calls")
        != CONTINUOUS_V3_V2_PRIOR_CODEX_FAMILY_DEBIT
        or debit.get("deepseek_calls") != CONTINUOUS_V3_V2_PRIOR_DEEPSEEK_DEBIT
        or debit.get("calls") != [CONTINUOUS_V3_CALL_SCHEDULE[0]]
    ):
        raise ContractValidationError("V2 historical one-call debit changed")
    debit_unsigned = dict(debit)
    debit_sha256 = debit_unsigned.pop("debit_sha256", None)
    required_debit_hashes = (
        "campaign_result_sha256",
        "campaign_execution_manifest_sha256",
        "run_result_sha256",
        "provider_ledger_sha256",
        "run_execution_manifest_sha256",
    )
    if (
        not re_is_sha256(debit_sha256)
        or debit_sha256 != canonical_sha256(debit_unsigned)
        or any(not re_is_sha256(debit.get(field)) for field in required_debit_hashes)
    ):
        raise ContractValidationError("V2 historical debit evidence changed")

    cycle = data.get("cycle_authority")
    if (
        not isinstance(cycle, Mapping)
        or not isinstance(cycle.get("cycle_id"), str)
        or not cycle["cycle_id"].strip()
        or type(cycle.get("cycle_sequence")) is not int
        or cycle["cycle_sequence"] < 1
        or not isinstance(cycle.get("job4_task_id"), str)
        or not cycle["job4_task_id"].strip()
    ):
        raise ContractValidationError("V2 cycle authority is incomplete")
    for field in (
        "checkpoint_git_sha",
        "authorization_record_sha256",
        "cycle_manifest_sha256",
        "manifest_root_sha256",
        "repository_identity_sha256",
        "task_set_sha256",
        "changed_source_manifest_sha256",
        "published_receipt_sha256",
        "job4_scope_sha256",
        "job4_started_receipt_sha256",
        "trigger_receipt_sha256",
        "cycle_state_sha256",
    ):
        candidate = cycle.get(field)
        if field == "checkpoint_git_sha":
            if not isinstance(candidate, str) or len(candidate) != 40:
                raise ContractValidationError("V2 checkpoint Git identity is invalid")
        elif not re_is_sha256(candidate):
            raise ContractValidationError("V2 cycle authority hash is invalid")
    if cycle.get("cycle_state") != "job4_in_progress":
        raise ContractValidationError("V2 cycle is not dispatch-authorized")

    required_mapping_blocks = (
        "fixture",
        "route",
        "providers",
        "prompt_bindings",
        "schema_bindings",
        "policy_bindings",
        "source_bindings",
        "transport_mode",
    )
    if any(not isinstance(data.get(field), Mapping) for field in required_mapping_blocks):
        raise ContractValidationError("V2 campaign binding block is invalid")
    if data["fixture"].get("call_schedule") != list(CONTINUOUS_V3_CALL_SCHEDULE):
        raise ContractValidationError("V2 campaign call schedule changed")
    fixture = data["fixture"]
    if (
        set(fixture)
        != {
            "messages",
            "fixture_sha256",
            "call_schedule",
            "call_schedule_sha256",
        }
        or fixture.get("fixture_sha256")
        != canonical_sha256(fixture.get("messages"))
        or fixture.get("call_schedule_sha256")
        != canonical_sha256(CONTINUOUS_V3_CALL_SCHEDULE)
    ):
        raise ContractValidationError("V2 campaign fixture binding changed")
    if data["providers"] != {
        "planner": {"model": "gpt-5.6-sol", "effort": "medium", "fast": False},
        "composer": {"model": "deepseek-v4-flash", "thinking": False},
        "validator": {"model": "gpt-5.6-terra", "effort": "high", "fast": False},
    }:
        raise ContractValidationError("V2 provider identities changed")
    route = data["route"]
    if (
        route.get("profile_id")
        != "cera.sillytavern.continuous_v3_test.v1"
        or route.get("profile_path")
        != "integrations/sillytavern/continuous_v3_test_profile.json"
        or route.get("host") != "127.0.0.1"
        or route.get("port") != 5113
        or route.get("model") != "cera-continuous-v3-test"
        or not re_is_sha256(route.get("profile_sha256"))
        or not isinstance(route.get("profile"), Mapping)
    ):
        raise ContractValidationError("V2 campaign route binding changed")
    policy = data["policy_bindings"]
    if (
        policy.get("accepted_snapshot_path_policy_sha256")
        != CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_SHA256
        or policy.get("strict_accept_only") is not True
        or policy.get("automatic_retry") is not False
        or policy.get("fallback") is not False
        or policy.get("automatic_false_positive") is not False
        or not re_is_sha256(policy.get("persistence_policy_sha256"))
    ):
        raise ContractValidationError("V2 campaign policy binding changed")
    source = data["source_bindings"]
    tracked = source.get("tracked_files")
    if (
        not isinstance(tracked, Mapping)
        or source.get("tracked_file_count") != len(tracked)
        or source.get("tracked_files_root_sha256") != canonical_sha256(tracked)
        or source.get("checkpoint_sha") != cycle.get("checkpoint_git_sha")
        or source.get("actual_head_sha") != cycle.get("checkpoint_git_sha")
        or not isinstance(source.get("actual_tree_sha"), str)
        or len(source["actual_tree_sha"]) != 40
        or not re_is_sha256(source.get("source_database_sha256"))
        or any(
            not isinstance(path, str) or not re_is_sha256(file_sha256)
            for path, file_sha256 in tracked.items()
        )
        or tracked.get(route.get("profile_path")) != route.get("profile_sha256")
    ):
        raise ContractValidationError("V2 campaign source binding changed")
    prompts = data["prompt_bindings"]
    schemas = data["schema_bindings"]
    if (
        any(
            not isinstance(prompts.get(field), str)
            or not prompts[field].strip()
            for field in (
                "planner_prompt_version",
                "composer_prompt_version",
                "validator_prompt_version",
            )
        )
        or any(
            not re_is_sha256(prompts.get(field))
            for field in (
                "planner_stable_instructions_sha256",
                "planner_base_instructions_sha256",
                "validator_stable_instructions_sha256",
                "validator_base_instructions_sha256",
            )
        )
        or any(
            not isinstance(value, str) or not value.strip()
            for value in schemas.values()
        )
        or schemas.get("campaign_configuration")
        != CONTINUOUS_V3_CAMPAIGN_CONFIGURATION_V2_SCHEMA
    ):
        raise ContractValidationError("V2 prompt or schema binding changed")
    transport = data["transport_mode"]
    if set(transport) != {
        "mode",
        "external_provider_calls_authorized",
        "provider_activation_relative_path",
        "provider_activation_file_sha256",
        "provider_activation_receipt_sha256",
        "fake_fixture_id",
        "fake_fixture_sha256",
        "provider_free_partial_failure",
    }:
        raise ContractValidationError("V2 transport authority fields changed")
    if transport.get("mode") not in {
        "external_provider",
        "non_network_fake_ports",
    }:
        raise ContractValidationError("V2 transport mode is invalid")
    if type(transport.get("external_provider_calls_authorized")) is not int:
        raise ContractValidationError("V2 transport authority is invalid")
    if transport["mode"] == "non_network_fake_ports":
        partial_failure = transport.get("provider_free_partial_failure")
        if (
            transport.get("external_provider_calls_authorized") != 0
            or transport.get("provider_activation_relative_path") is not None
            or transport.get("provider_activation_file_sha256") is not None
            or transport.get("provider_activation_receipt_sha256") is not None
            or not isinstance(transport.get("fake_fixture_id"), str)
            or not re_is_sha256(transport.get("fake_fixture_sha256"))
        ):
            raise ContractValidationError("V2 fake transport authority changed")
        if partial_failure is not None:
            expected_partial_fields = {
                "schema_version",
                "transport_mode",
                "failure_boundary",
                "stage_invocations_before_failure",
                "external_provider_calls",
                "retry_permitted",
                "run_id",
                "fixture_sha256",
            }
            if (
                not isinstance(partial_failure, Mapping)
                or set(partial_failure) != expected_partial_fields
                or partial_failure.get("schema_version")
                != "cera.sillytavern_continuous_v3_partial_failure_fixture.v1"
                or partial_failure.get("transport_mode")
                != "non_network_fake_ports"
                or partial_failure.get("failure_boundary")
                != "after_first_turn_three_stage_dispatch_before_turn_2"
                or partial_failure.get("stage_invocations_before_failure") != 3
                or partial_failure.get("external_provider_calls") != 0
                or partial_failure.get("retry_permitted") is not False
                or partial_failure.get("run_id")
                not in CONTINUOUS_V3_V2_RUN_IDENTITIES
                or partial_failure.get("fixture_sha256")
                != PROVIDER_FREE_PARTIAL_FAILURE_FIXTURE_SHA256
            ):
                raise ContractValidationError(
                    "V2 provider-free partial failure fixture changed"
                )
    elif (
        transport.get("external_provider_calls_authorized") < 1
        or not isinstance(transport.get("provider_activation_relative_path"), str)
        or not re_is_sha256(transport.get("provider_activation_file_sha256"))
        or not re_is_sha256(transport.get("provider_activation_receipt_sha256"))
        or transport.get("fake_fixture_id") is not None
        or transport.get("fake_fixture_sha256") is not None
        or transport.get("provider_free_partial_failure") is not None
    ):
        raise ContractValidationError("V2 external transport authority changed")

    configuration_sha256 = data.get("configuration_sha256")
    unsigned = dict(data)
    unsigned.pop("configuration_sha256", None)
    if (
        not re_is_sha256(configuration_sha256)
        or configuration_sha256 != canonical_sha256(unsigned)
    ):
        raise ContractValidationError("V2 campaign configuration binding changed")
    return data


def _provider_family(label: str) -> str:
    if label not in CONTINUOUS_V3_CALL_SCHEDULE:
        raise ContractValidationError("campaign call label is unknown")
    return "deepseek" if "deepseek" in label else "codex_family"


class CampaignRunState(str, Enum):
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CampaignRunRecord:
    run_id: str
    state: CampaignRunState
    execution_identity_sha256: str
    calls: tuple[str, ...] = ()
    terminal_reason: str | None = None


@dataclass(slots=True)
class ContinuousV3TwoRunCampaign:
    execution_identity_sha256: str
    runs: list[CampaignRunRecord] = field(default_factory=list)
    consecutive_passes: int = 0
    total_provider_calls: int = 0
    restart_after_last_pass: bool = False
    controlled_restart_count: int = 0
    run_identities: tuple[str, ...] = CONTINUOUS_V3_RUN_IDENTITIES
    total_call_ceiling: int = CAMPAIGN_TOTAL_CALL_CEILING
    codex_family_call_ceiling: int = CODEX_FAMILY_CALL_CEILING
    deepseek_call_ceiling: int = DEEPSEEK_CALL_CEILING

    def __post_init__(self) -> None:
        if not re_is_sha256(self.execution_identity_sha256):
            raise ContractValidationError("campaign execution identity is invalid")
        if type(self.total_provider_calls) is not int or self.total_provider_calls < 0:
            raise ContractValidationError("campaign provider-call count is invalid")
        if type(self.consecutive_passes) is not int or not 0 <= self.consecutive_passes <= 2:
            raise ContractValidationError("campaign consecutive-pass count is invalid")
        if type(self.restart_after_last_pass) is not bool:
            raise ContractValidationError("campaign restart state is invalid")
        if (
            type(self.controlled_restart_count) is not int
            or not 0 <= self.controlled_restart_count <= 3
        ):
            raise ContractValidationError("campaign restart count is invalid")
        if (
            len(self.run_identities) != 4
            or len(set(self.run_identities)) != 4
            or any(not isinstance(value, str) or not value.strip() for value in self.run_identities)
        ):
            raise ContractValidationError("campaign run identities are invalid")
        if any(
            type(value) is not int or value < 0
            for value in (
                self.total_call_ceiling,
                self.codex_family_call_ceiling,
                self.deepseek_call_ceiling,
            )
        ):
            raise ContractValidationError("campaign call ceiling is invalid")
        if tuple(run.run_id for run in self.runs) != self.run_identities[
            : len(self.runs)
        ]:
            raise ContractValidationError("campaign run history is out of order")
        recorded_calls = tuple(call for run in self.runs for call in run.calls)
        for call in recorded_calls:
            _provider_family(call)
        if self.total_provider_calls != len(recorded_calls):
            raise ContractValidationError(
                "campaign cumulative provider-call count disagrees with run history"
            )
        if self.total_provider_calls > self.total_call_ceiling:
            raise ContractValidationError("campaign provider-call ceiling is exceeded")
        if self.codex_family_calls > self.codex_family_call_ceiling:
            raise ContractValidationError("campaign Codex-family ceiling is exceeded")
        if self.deepseek_calls > self.deepseek_call_ceiling:
            raise ContractValidationError("campaign DeepSeek ceiling is exceeded")
        tail_passes = 0
        terminal_history = self.runs[:-1] if self.current is not None else self.runs
        for run in terminal_history:
            if (
                run.state is CampaignRunState.PASSED
                and run.execution_identity_sha256 == self.execution_identity_sha256
            ):
                tail_passes += 1
            else:
                tail_passes = 0
        if tail_passes != self.consecutive_passes:
            raise ContractValidationError(
                "campaign consecutive-pass count disagrees with run history"
            )
        if self.restart_after_last_pass and (
            self.consecutive_passes != 1 or self.current is not None
        ):
            raise ContractValidationError("campaign restart state is contradictory")

    @property
    def complete(self) -> bool:
        return self.consecutive_passes == 2

    @property
    def current(self) -> CampaignRunRecord | None:
        if self.runs and self.runs[-1].state is CampaignRunState.RUNNING:
            return self.runs[-1]
        return None

    @property
    def codex_family_calls(self) -> int:
        return sum(
            _provider_family(call) == "codex_family"
            for run in self.runs
            for call in run.calls
        )

    @property
    def deepseek_calls(self) -> int:
        return sum(
            _provider_family(call) == "deepseek"
            for run in self.runs
            for call in run.calls
        )

    def begin_run(self, run_id: str, *, execution_identity_sha256: str) -> None:
        if self.complete or self.current is not None:
            raise StateConflictError("campaign cannot start another run")
        expected = self.run_identities[len(self.runs)] if len(self.runs) < 4 else None
        if run_id != expected:
            raise StateConflictError("campaign run identity is out of order or exhausted")
        if execution_identity_sha256 != self.execution_identity_sha256:
            self.consecutive_passes = 0
            self.restart_after_last_pass = False
            raise StateConflictError("qualifying execution bytes changed")
        if self.consecutive_passes == 1 and not self.restart_after_last_pass:
            raise StateConflictError("controlled adapter restart is required")
        self.runs.append(
            CampaignRunRecord(
                run_id=run_id,
                state=CampaignRunState.RUNNING,
                execution_identity_sha256=execution_identity_sha256,
            )
        )
        self.restart_after_last_pass = False

    def record_dispatch(self, label: str) -> None:
        current = self.current
        if current is None:
            raise StateConflictError("provider dispatch has no active run")
        expected_index = len(current.calls)
        if expected_index >= len(CONTINUOUS_V3_CALL_SCHEDULE):
            raise StateConflictError("run provider-call ceiling is exhausted")
        if label != CONTINUOUS_V3_CALL_SCHEDULE[expected_index]:
            raise StateConflictError("run provider-call order changed")
        family = _provider_family(label)
        if self.total_provider_calls >= self.total_call_ceiling:
            raise StateConflictError("campaign provider-call ceiling is exhausted")
        if (
            family == "codex_family"
            and self.codex_family_calls >= self.codex_family_call_ceiling
        ):
            raise StateConflictError("campaign Codex-family ceiling is exhausted")
        if family == "deepseek" and self.deepseek_calls >= self.deepseek_call_ceiling:
            raise StateConflictError("campaign DeepSeek ceiling is exhausted")
        self.total_provider_calls += 1
        self.runs[-1] = CampaignRunRecord(
            run_id=current.run_id,
            state=current.state,
            execution_identity_sha256=current.execution_identity_sha256,
            calls=(*current.calls, label),
        )

    def terminalize(self, *, passed: bool, reason: str) -> CampaignRunRecord:
        current = self.current
        if current is None:
            raise StateConflictError("campaign has no active run to terminalize")
        if not reason.strip():
            raise ContractValidationError("campaign terminal reason is empty")
        if passed and current.calls != CONTINUOUS_V3_CALL_SCHEDULE:
            raise StateConflictError("passing run lacks the exact ten-call schedule")
        state = CampaignRunState.PASSED if passed else CampaignRunState.FAILED
        terminal = CampaignRunRecord(
            run_id=current.run_id,
            state=state,
            execution_identity_sha256=current.execution_identity_sha256,
            calls=current.calls,
            terminal_reason=reason,
        )
        self.runs[-1] = terminal
        if passed:
            self.consecutive_passes += 1
        else:
            self.consecutive_passes = 0
        self.restart_after_last_pass = False
        return terminal

    def record_controlled_restart(self, *, execution_identity_sha256: str) -> None:
        if (
            self.current is not None
            or self.consecutive_passes != 1
            or execution_identity_sha256 != self.execution_identity_sha256
        ):
            raise StateConflictError("controlled restart is outside the pass boundary")
        self.restart_after_last_pass = True
        self.controlled_restart_count += 1

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": "cera.sillytavern_continuous_v3_campaign.v1",
            "execution_identity_sha256": self.execution_identity_sha256,
            "consecutive_passes": self.consecutive_passes,
            "total_provider_calls": self.total_provider_calls,
            "codex_family_calls": self.codex_family_calls,
            "deepseek_calls": self.deepseek_calls,
            "restart_after_last_pass": self.restart_after_last_pass,
            "controlled_restart_count": self.controlled_restart_count,
            "runs": [
                {
                    "run_id": record.run_id,
                    "state": record.state.value,
                    "execution_identity_sha256": record.execution_identity_sha256,
                    "calls": list(record.calls),
                    "terminal_reason": record.terminal_reason,
                }
                for record in self.runs
            ],
        }
        if self.run_identities != CONTINUOUS_V3_RUN_IDENTITIES:
            payload["run_identities"] = list(self.run_identities)
        if self.total_call_ceiling != CAMPAIGN_TOTAL_CALL_CEILING:
            payload["total_call_ceiling"] = self.total_call_ceiling
        if self.codex_family_call_ceiling != CODEX_FAMILY_CALL_CEILING:
            payload["codex_family_call_ceiling"] = self.codex_family_call_ceiling
        if self.deepseek_call_ceiling != DEEPSEEK_CALL_CEILING:
            payload["deepseek_call_ceiling"] = self.deepseek_call_ceiling
        payload["campaign_state_sha256"] = canonical_sha256(payload)
        return payload
