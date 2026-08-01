from __future__ import annotations

import unittest

from cera.errors import ContractValidationError
from cera.providers import (
    CodexOperationTelemetryV1,
    CodexUsageAccumulator,
    CodexUsageStepV1,
)
from cera.serialization import text_sha256


class CodexOperationObservabilityTests(unittest.TestCase):
    def telemetry(self) -> CodexOperationTelemetryV1:
        step = CodexUsageStepV1(
            sequence=1,
            observed_at_unix_us=20,
            input_tokens=100,
            cached_input_tokens=80,
            uncached_input_tokens=20,
            output_tokens=10,
            reasoning_tokens=4,
            thread_total_input_tokens=900,
            thread_total_cached_input_tokens=700,
            thread_total_output_tokens=50,
            thread_total_reasoning_tokens=20,
        )
        return CodexOperationTelemetryV1(
            schema_version=CodexOperationTelemetryV1.SCHEMA_VERSION,
            request_sha256=None,
            provider_operation_id_sha256=text_sha256("operation"),
            provider_thread_id_sha256=text_sha256("candidate-thread"),
            provider_root_thread_id_sha256=None,
            accepted_parent_checkpoint_id_sha256=None,
            candidate_checkpoint_id_sha256=None,
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            fast_mode_enabled=False,
            packet_ready_unix_us=None,
            request_start_unix_us=10,
            first_reasoning_item_unix_us=12,
            first_structured_output_item_unix_us=18,
            final_evidence_result_unix_us=16,
            provider_completion_unix_us=25,
            python_parse_start_unix_us=None,
            python_parse_completion_unix_us=None,
            python_validation_start_unix_us=None,
            python_validation_completion_unix_us=None,
            usage_steps=(step,),
            cumulative_input_tokens=100,
            cumulative_cached_input_tokens=80,
            cumulative_uncached_input_tokens=20,
            cumulative_output_tokens=10,
            cumulative_reasoning_tokens=4,
            tool_timings=(),
            tool_call_count=0,
            provider_attempt_count=1,
            finish_status="completed",
            transport_error=None,
            unsupported_fields=(
                "request_sha256",
                "packet_ready_unix_us",
                "provider_root_thread_id_sha256",
                "accepted_parent_checkpoint_id_sha256",
                "candidate_checkpoint_id_sha256",
                "python_parse_start_unix_us",
                "python_parse_completion_unix_us",
                "python_validation_start_unix_us",
                "python_validation_completion_unix_us",
            ),
            retains_prompt=False,
            retains_output=False,
            retains_reasoning=False,
            retains_tool_arguments=False,
        )

    def test_unique_usage_steps_are_summed_and_repeated_totals_are_not(self) -> None:
        usage = CodexUsageAccumulator()
        self.assertTrue(
            usage.observe(
                observed_at_unix_us=100,
                last={
                    "input_tokens": 65_451,
                    "cached_input_tokens": 0,
                    "output_tokens": 402,
                    "reasoning_output_tokens": 366,
                },
                total={
                    "input_tokens": 379_004,
                    "cached_input_tokens": 228_608,
                    "output_tokens": 9_570,
                    "reasoning_output_tokens": 4_186,
                },
            )
        )
        self.assertFalse(
            usage.observe(
                observed_at_unix_us=101,
                last={
                    "input_tokens": 65_451,
                    "cached_input_tokens": 0,
                    "output_tokens": 402,
                    "reasoning_output_tokens": 366,
                },
                total={
                    "input_tokens": 379_004,
                    "cached_input_tokens": 228_608,
                    "output_tokens": 9_570,
                    "reasoning_output_tokens": 4_186,
                },
            )
        )
        for observed, last, total in (
            (102, (66_865, 65_280, 121, 19), (445_869, 293_888, 9_691, 4_205)),
            (103, (73_343, 66_304, 87, 8), (519_212, 360_192, 9_778, 4_213)),
            (104, (75_520, 72_448, 4_694, 1_778), (594_732, 432_640, 14_472, 5_991)),
        ):
            self.assertTrue(
                usage.observe(
                    observed_at_unix_us=observed,
                    last={
                        "input_tokens": last[0],
                        "cached_input_tokens": last[1],
                        "output_tokens": last[2],
                        "reasoning_output_tokens": last[3],
                    },
                    total={
                        "input_tokens": total[0],
                        "cached_input_tokens": total[1],
                        "output_tokens": total[2],
                        "reasoning_output_tokens": total[3],
                    },
                )
            )
        self.assertEqual(
            usage.cumulative,
            (281_179, 204_032, 77_147, 5_304, 2_171),
        )
        self.assertEqual(len(usage.steps), 4)

    def test_unknown_is_not_zero_and_backwards_total_is_rejected(self) -> None:
        usage = CodexUsageAccumulator()
        self.assertIsNone(usage.cumulative)
        usage.observe(
            observed_at_unix_us=1,
            last={
                "input_tokens": 10,
                "cached_input_tokens": 5,
                "output_tokens": 2,
                "reasoning_output_tokens": 1,
            },
            total={
                "input_tokens": 20,
                "cached_input_tokens": 10,
                "output_tokens": 4,
                "reasoning_output_tokens": 2,
            },
        )
        with self.assertRaisesRegex(ContractValidationError, "moved backwards"):
            usage.observe(
                observed_at_unix_us=2,
                last={
                    "input_tokens": 1,
                    "cached_input_tokens": 0,
                    "output_tokens": 1,
                    "reasoning_output_tokens": 0,
                },
                total={
                    "input_tokens": 19,
                    "cached_input_tokens": 10,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 2,
                },
            )

    def test_identity_and_python_phase_bindings_remove_only_supported_unknowns(self) -> None:
        telemetry = (
            self.telemetry()
            .bind_request(text_sha256("request"))
            .bind_parse_phase(
                parse_start_unix_us=30,
                parse_completion_unix_us=31,
            )
            .bind_reasoner_context(
                packet_ready_unix_us=9,
                provider_root_thread_id_sha256=text_sha256("root-thread"),
                accepted_parent_checkpoint_id_sha256=text_sha256("parent"),
                candidate_checkpoint_id_sha256=text_sha256("candidate"),
            )
            .bind_validation_phase(
                validation_start_unix_us=32,
                validation_completion_unix_us=35,
            )
        )
        self.assertEqual(telemetry.unsupported_fields, ())
        self.assertEqual(telemetry.cumulative_uncached_input_tokens, 20)
        self.assertFalse(telemetry.fast_mode_enabled)

    def test_transport_error_is_explicit_without_retaining_content(self) -> None:
        telemetry = self.telemetry().with_transport_error(
            "CERA_REASONER_CONTRACT_INVALID"
        )
        self.assertEqual(
            telemetry.transport_error,
            "CERA_REASONER_CONTRACT_INVALID",
        )
        self.assertFalse(telemetry.retains_prompt)
        self.assertFalse(telemetry.retains_output)


if __name__ == "__main__":
    unittest.main()
