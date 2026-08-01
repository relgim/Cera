"""Privacy-safe, cumulative observability for one Codex provider operation.

The Codex SDK publishes thread-cumulative token notifications after individual
model steps.  CERA needs operation-local totals, so this module de-duplicates
identical notifications and sums each unique ``last`` breakdown exactly once.
It never retains prompts, tool arguments, reasoning text, or provider output.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import ClassVar, Mapping

from cera.errors import ContractValidationError
from cera.schema import require_schema
from cera.serialization import domain_sha256, re_is_sha256


def _optional_nonnegative(value: int | None, field: str) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ContractValidationError(f"{field} must be non-negative or null")


@dataclass(frozen=True, slots=True)
class CodexUsageStepV1:
    sequence: int
    observed_at_unix_us: int
    input_tokens: int
    cached_input_tokens: int
    uncached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    thread_total_input_tokens: int
    thread_total_cached_input_tokens: int
    thread_total_output_tokens: int
    thread_total_reasoning_tokens: int

    def __post_init__(self) -> None:
        for field in self.__dataclass_fields__:
            value = getattr(self, field)
            if type(value) is not int or value < 0:
                raise ContractValidationError(
                    f"Codex usage step {field} must be non-negative"
                )
        if self.sequence < 1:
            raise ContractValidationError("Codex usage step sequence must be positive")
        if self.cached_input_tokens > self.input_tokens:
            raise ContractValidationError("Codex usage step cached input exceeds input")
        if self.uncached_input_tokens != self.input_tokens - self.cached_input_tokens:
            raise ContractValidationError("Codex usage step uncached input is inconsistent")


@dataclass(frozen=True, slots=True)
class CodexToolTimingV1:
    sequence: int
    server_name: str
    tool_name: str
    started_at_unix_us: int | None
    completed_at_unix_us: int | None
    status: str

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ContractValidationError("Codex tool timing sequence must be positive")
        if not self.server_name.strip() or not self.tool_name.strip():
            raise ContractValidationError("Codex tool timing requires tool identity")
        if not self.status.strip():
            raise ContractValidationError("Codex tool timing requires status")
        _optional_nonnegative(self.started_at_unix_us, "started_at_unix_us")
        _optional_nonnegative(self.completed_at_unix_us, "completed_at_unix_us")
        if (
            self.started_at_unix_us is not None
            and self.completed_at_unix_us is not None
            and self.completed_at_unix_us < self.started_at_unix_us
        ):
            raise ContractValidationError("Codex tool completion precedes start")


@dataclass(frozen=True, slots=True)
class CodexOperationTelemetryV1:
    """Versioned content-free timing and usage record for one Reasoner call."""

    SCHEMA_VERSION: ClassVar[str] = "cera.codex_operation_telemetry.v1"

    schema_version: str
    request_sha256: str | None
    provider_operation_id_sha256: str
    provider_thread_id_sha256: str
    provider_root_thread_id_sha256: str | None
    accepted_parent_checkpoint_id_sha256: str | None
    candidate_checkpoint_id_sha256: str | None
    model: str
    reasoning_effort: str
    fast_mode_enabled: bool
    packet_ready_unix_us: int | None
    request_start_unix_us: int
    first_reasoning_item_unix_us: int | None
    first_structured_output_item_unix_us: int | None
    final_evidence_result_unix_us: int | None
    provider_completion_unix_us: int
    python_parse_start_unix_us: int | None
    python_parse_completion_unix_us: int | None
    python_validation_start_unix_us: int | None
    python_validation_completion_unix_us: int | None
    usage_steps: tuple[CodexUsageStepV1, ...]
    cumulative_input_tokens: int | None
    cumulative_cached_input_tokens: int | None
    cumulative_uncached_input_tokens: int | None
    cumulative_output_tokens: int | None
    cumulative_reasoning_tokens: int | None
    tool_timings: tuple[CodexToolTimingV1, ...]
    tool_call_count: int
    provider_attempt_count: int
    finish_status: str
    transport_error: str | None
    unsupported_fields: tuple[str, ...]
    retains_prompt: bool
    retains_output: bool
    retains_reasoning: bool
    retains_tool_arguments: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        for value, field in (
            (self.request_sha256, "request_sha256"),
            (self.provider_operation_id_sha256, "provider_operation_id_sha256"),
            (self.provider_thread_id_sha256, "provider_thread_id_sha256"),
            (self.provider_root_thread_id_sha256, "provider_root_thread_id_sha256"),
            (
                self.accepted_parent_checkpoint_id_sha256,
                "accepted_parent_checkpoint_id_sha256",
            ),
            (self.candidate_checkpoint_id_sha256, "candidate_checkpoint_id_sha256"),
        ):
            if value is not None and not re_is_sha256(value):
                raise ContractValidationError(f"{field} must be SHA-256 or null")
        if not self.model.strip() or not self.reasoning_effort.strip():
            raise ContractValidationError("Codex telemetry requires model and effort")
        if self.fast_mode_enabled:
            raise ContractValidationError("CERA Codex telemetry cannot report Fast mode")
        for field in (
            "packet_ready_unix_us",
            "request_start_unix_us",
            "first_reasoning_item_unix_us",
            "first_structured_output_item_unix_us",
            "final_evidence_result_unix_us",
            "provider_completion_unix_us",
            "python_parse_start_unix_us",
            "python_parse_completion_unix_us",
            "python_validation_start_unix_us",
            "python_validation_completion_unix_us",
            "cumulative_input_tokens",
            "cumulative_cached_input_tokens",
            "cumulative_uncached_input_tokens",
            "cumulative_output_tokens",
            "cumulative_reasoning_tokens",
        ):
            _optional_nonnegative(getattr(self, field), field)
        if self.provider_completion_unix_us < self.request_start_unix_us:
            raise ContractValidationError("Codex completion precedes request start")
        if self.packet_ready_unix_us is not None and (
            self.packet_ready_unix_us > self.request_start_unix_us
        ):
            raise ContractValidationError("Codex packet-ready time follows request start")
        for start, completion, label in (
            (
                self.python_parse_start_unix_us,
                self.python_parse_completion_unix_us,
                "parse",
            ),
            (
                self.python_validation_start_unix_us,
                self.python_validation_completion_unix_us,
                "validation",
            ),
        ):
            if (start is None) != (completion is None):
                raise ContractValidationError(f"Codex {label} timing must be paired")
            if start is not None and completion is not None and completion < start:
                raise ContractValidationError(f"Codex {label} completion precedes start")
        totals = (
            self.cumulative_input_tokens,
            self.cumulative_cached_input_tokens,
            self.cumulative_uncached_input_tokens,
            self.cumulative_output_tokens,
            self.cumulative_reasoning_tokens,
        )
        if self.usage_steps:
            expected = (
                sum(value.input_tokens for value in self.usage_steps),
                sum(value.cached_input_tokens for value in self.usage_steps),
                sum(value.uncached_input_tokens for value in self.usage_steps),
                sum(value.output_tokens for value in self.usage_steps),
                sum(value.reasoning_tokens for value in self.usage_steps),
            )
            if totals != expected:
                raise ContractValidationError("Codex cumulative usage does not match steps")
        elif any(value is not None for value in totals):
            raise ContractValidationError("Codex cumulative usage requires usage steps")
        if self.tool_call_count != len(self.tool_timings):
            raise ContractValidationError("Codex telemetry tool count is inconsistent")
        if self.provider_attempt_count != 1:
            raise ContractValidationError("Codex telemetry requires exactly one attempt")
        if not self.finish_status.strip():
            raise ContractValidationError("Codex telemetry requires finish status")
        if len(self.unsupported_fields) != len(set(self.unsupported_fields)):
            raise ContractValidationError("Codex unsupported fields are duplicated")
        if any(
            (
                self.retains_prompt,
                self.retains_output,
                self.retains_reasoning,
                self.retains_tool_arguments,
            )
        ):
            raise ContractValidationError("Codex telemetry cannot retain provider content")

    @property
    def telemetry_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)

    def bind_request(self, request_sha256: str) -> "CodexOperationTelemetryV1":
        return replace(
            self,
            request_sha256=request_sha256,
            unsupported_fields=tuple(
                value for value in self.unsupported_fields if value != "request_sha256"
            ),
        )

    def bind_reasoner_context(
        self,
        *,
        packet_ready_unix_us: int,
        provider_root_thread_id_sha256: str | None,
        accepted_parent_checkpoint_id_sha256: str | None,
        candidate_checkpoint_id_sha256: str | None,
    ) -> "CodexOperationTelemetryV1":
        supported = {"packet_ready_unix_us"}
        if provider_root_thread_id_sha256 is not None:
            supported.add("provider_root_thread_id_sha256")
        if accepted_parent_checkpoint_id_sha256 is not None:
            supported.add("accepted_parent_checkpoint_id_sha256")
        if candidate_checkpoint_id_sha256 is not None:
            supported.add("candidate_checkpoint_id_sha256")
        return replace(
            self,
            packet_ready_unix_us=packet_ready_unix_us,
            provider_root_thread_id_sha256=provider_root_thread_id_sha256,
            accepted_parent_checkpoint_id_sha256=(
                accepted_parent_checkpoint_id_sha256
            ),
            candidate_checkpoint_id_sha256=candidate_checkpoint_id_sha256,
            unsupported_fields=tuple(
                value
                for value in self.unsupported_fields
                if value not in supported
            ),
        )

    def bind_parse_phase(
        self,
        *,
        parse_start_unix_us: int,
        parse_completion_unix_us: int,
    ) -> "CodexOperationTelemetryV1":
        return replace(
            self,
            python_parse_start_unix_us=parse_start_unix_us,
            python_parse_completion_unix_us=parse_completion_unix_us,
            unsupported_fields=tuple(
                value
                for value in self.unsupported_fields
                if value
                not in {
                    "python_parse_start_unix_us",
                    "python_parse_completion_unix_us",
                }
            ),
        )

    def with_transport_error(self, error_code: str) -> "CodexOperationTelemetryV1":
        if not isinstance(error_code, str) or not error_code.strip():
            raise ContractValidationError("Codex transport error code is required")
        return replace(self, transport_error=error_code)

    def bind_python_phases(
        self,
        *,
        parse_start_unix_us: int,
        parse_completion_unix_us: int,
        validation_start_unix_us: int,
        validation_completion_unix_us: int,
    ) -> "CodexOperationTelemetryV1":
        return replace(
            self,
            python_parse_start_unix_us=parse_start_unix_us,
            python_parse_completion_unix_us=parse_completion_unix_us,
            python_validation_start_unix_us=validation_start_unix_us,
            python_validation_completion_unix_us=validation_completion_unix_us,
            unsupported_fields=tuple(
                value
                for value in self.unsupported_fields
                if value
                not in {
                    "python_parse_start_unix_us",
                    "python_parse_completion_unix_us",
                    "python_validation_start_unix_us",
                    "python_validation_completion_unix_us",
                }
            ),
        )

    def bind_validation_phase(
        self,
        *,
        validation_start_unix_us: int,
        validation_completion_unix_us: int,
    ) -> "CodexOperationTelemetryV1":
        return replace(
            self,
            python_validation_start_unix_us=validation_start_unix_us,
            python_validation_completion_unix_us=validation_completion_unix_us,
            unsupported_fields=tuple(
                value
                for value in self.unsupported_fields
                if value
                not in {
                    "python_validation_start_unix_us",
                    "python_validation_completion_unix_us",
                }
            ),
        )


class CodexUsageAccumulator:
    """Collect unique SDK thread-total notifications without double counting."""

    def __init__(self) -> None:
        self._last_thread_total: tuple[int, int, int, int] | None = None
        self._steps: list[CodexUsageStepV1] = []

    def observe(
        self,
        *,
        observed_at_unix_us: int,
        last: Mapping[str, int],
        total: Mapping[str, int],
    ) -> bool:
        names = (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        )
        try:
            last_values = tuple(last[name] for name in names)
            total_values = tuple(total[name] for name in names)
        except (KeyError, TypeError):
            raise ContractValidationError("Codex usage notification is incomplete") from None
        if any(type(value) is not int or value < 0 for value in (*last_values, *total_values)):
            raise ContractValidationError("Codex usage notification is invalid")
        if total_values == self._last_thread_total:
            return False
        if self._last_thread_total is not None and any(
            current < prior
            for current, prior in zip(total_values, self._last_thread_total, strict=True)
        ):
            raise ContractValidationError("Codex thread usage moved backwards")
        input_tokens, cached_tokens, output_tokens, reasoning_tokens = last_values
        self._steps.append(
            CodexUsageStepV1(
                sequence=len(self._steps) + 1,
                observed_at_unix_us=observed_at_unix_us,
                input_tokens=input_tokens,
                cached_input_tokens=cached_tokens,
                uncached_input_tokens=input_tokens - cached_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                thread_total_input_tokens=total_values[0],
                thread_total_cached_input_tokens=total_values[1],
                thread_total_output_tokens=total_values[2],
                thread_total_reasoning_tokens=total_values[3],
            )
        )
        self._last_thread_total = total_values
        return True

    @property
    def steps(self) -> tuple[CodexUsageStepV1, ...]:
        return tuple(self._steps)

    @property
    def cumulative(self) -> tuple[int, int, int, int, int] | None:
        if not self._steps:
            return None
        return (
            sum(value.input_tokens for value in self._steps),
            sum(value.cached_input_tokens for value in self._steps),
            sum(value.uncached_input_tokens for value in self._steps),
            sum(value.output_tokens for value in self._steps),
            sum(value.reasoning_tokens for value in self._steps),
        )
