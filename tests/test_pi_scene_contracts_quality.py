from __future__ import annotations

from dataclasses import replace
import unittest

from cera.errors import ContractValidationError
from cera.pi_scene.contracts import (
    LeanRecordingAttemptV1,
    PiWriterReceiptV1,
    RecordingStatus,
    SceneRoute,
)
from cera.serialization import text_sha256


def _writer_receipt() -> PiWriterReceiptV1:
    return PiWriterReceiptV1(
        schema_version=PiWriterReceiptV1.SCHEMA_VERSION,
        route=SceneRoute.ORDINARY,
        provider="test-provider",
        model="test-model",
        pi_version="test-pi",
        session_id_sha256=text_sha256("session"),
        parent_session_id_sha256=None,
        request_sha256=text_sha256("request"),
        output_sha256=text_sha256("accepted prose"),
        provider_operations=1,
        tool_call_count=0,
        failed_tool_call_count=0,
        input_tokens=10,
        cached_input_tokens=0,
        output_tokens=4,
        reasoning_tokens=0,
        duration_ms=2,
        finish_status="stop",
        rehydrated=False,
    )


class PiSceneContractQualityTests(unittest.TestCase):
    def test_writer_receipt_rehydrated_requires_an_exact_boolean(self) -> None:
        receipt = _writer_receipt()
        for invalid in (0, 1, "false", None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ContractValidationError):
                    replace(receipt, rehydrated=invalid)  # type: ignore[arg-type]

    def test_writer_receipt_integer_counters_reject_boolean_values(self) -> None:
        with self.assertRaises(ContractValidationError):
            replace(_writer_receipt(), provider_operations=True)

    def test_phase_one_recording_state_is_the_only_valid_attempt_zero(self) -> None:
        pending = LeanRecordingAttemptV1(
            schema_version=LeanRecordingAttemptV1.SCHEMA_VERSION,
            accepted_turn_id="accepted-1",
            attempt_number=0,
            status=RecordingStatus.PROJECTION_PENDING,
            recorder_request_sha256=text_sha256("phase-one"),
            recorder_output_sha256=None,
            provider_operations=0,
        )
        self.assertEqual(pending.attempt_number, 0)

        invalid_changes = (
            {"attempt_number": 1},
            {"provider_operations": 1},
            {"recorder_output_sha256": text_sha256("output")},
            {"failure_code": "unexpected"},
            {
                "status": RecordingStatus.PENDING_REPAIR,
                "failure_code": "failure",
            },
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                with self.assertRaises(ContractValidationError):
                    replace(pending, **changes)


if __name__ == "__main__":
    unittest.main()
