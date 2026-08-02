"""Immutable accounting for the bounded Continuous V3 SillyTavern campaign."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, re_is_sha256


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

CAMPAIGN_TOTAL_CALL_CEILING = 40
CODEX_FAMILY_CALL_CEILING = 800
DEEPSEEK_CALL_CEILING = 800


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

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": "cera.sillytavern_continuous_v3_campaign.v1",
            "execution_identity_sha256": self.execution_identity_sha256,
            "consecutive_passes": self.consecutive_passes,
            "total_provider_calls": self.total_provider_calls,
            "codex_family_calls": self.codex_family_calls,
            "deepseek_calls": self.deepseek_calls,
            "restart_after_last_pass": self.restart_after_last_pass,
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
