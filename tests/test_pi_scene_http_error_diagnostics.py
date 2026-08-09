from __future__ import annotations

import unittest

from cera.pi_scene.http import _typed_error_payload


class PiSceneHttpErrorDiagnosticsTests(unittest.TestCase):
    def test_known_error_explains_effect_and_next_action(self) -> None:
        payload = _typed_error_payload(
            error_code="CERA_INTAKE_INVALID",
            message="The request failed contract validation.",
            technical_detail="ContractValidationError: adult craft mode is invalid",
            next_action="correct_the_reported_request_field",
            debug_log_path=r"D:\runtime\debug\entry.md",
            trace_id="trace:fixed",
        )

        self.assertFalse(payload["story_state_committed"])
        error = payload["error"]
        self.assertFalse(error["accepted_state_changed"])
        self.assertFalse(error["provider_operation_submitted"])
        self.assertEqual(error["trace_id"], "trace:fixed")
        self.assertEqual(error["next_action"], "correct_the_reported_request_field")
        self.assertNotIn("adult craft mode is invalid", str(error))
        self.assertEqual(
            error["details"],
            ["Technical detail is available in the local debug log."],
        )
        self.assertEqual(error["debug_log_path"], r"D:\runtime\debug\entry.md")


if __name__ == "__main__":
    unittest.main()
