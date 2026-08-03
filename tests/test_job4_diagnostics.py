from __future__ import annotations

from copy import deepcopy
import io
import json
import unittest

from cera.continuous.job4_diagnostics import (
    ContinuousJob4SourceFrameV1,
    ContinuousJob4TestDiagnosticsV1,
    ContinuousJob4TestRecordV1,
    run_job4_unittest_diagnostics,
)
from cera.continuous.job4_terminal import (
    ContinuousJob4TerminalEvidenceV6,
    decode_continuous_job4_terminal_evidence,
)
from cera.serialization import canonical_sha256, text_sha256
from scripts.run_continuous_planner_validator_job4 import (
    build_canonical_job4_result,
    validate_declared_unittest_ids,
)
from tests.test_continuous_job4_harness import terminalized_detail
from tools.pro_review_cycle_core import (
    CycleError,
    validate_job4_result_contract,
)


class _Fixtures:
    class Recorder(unittest.TestCase):
        def test_pass(self) -> None:
            self.assertTrue(True)

        def test_failure(self) -> None:
            self.fail("private failure payload must not enter evidence")

        def test_error(self) -> None:
            raise RuntimeError("private error payload must not enter evidence")

        @unittest.skip("Windows fixture operation is unavailable")
        def test_skip(self) -> None:
            self.fail("unreachable")


def _passing_record(index: int, suffix: str) -> ContinuousJob4TestRecordV1:
    return ContinuousJob4TestRecordV1(
        index=index,
        test_id=(
            "tests.test_job4_diagnostics._Fixtures.Recorder.test_" + suffix
        ),
        status="passed",
        elapsed_ns=index,
    )


class ContinuousJob4DiagnosticsTests(unittest.TestCase):
    def test_module_target_expands_to_exact_ordered_test_ids(self) -> None:
        result, diagnostics = run_job4_unittest_diagnostics(
            ("tests.test_job4_diagnostics._Fixtures.Recorder",),
            stream=io.StringIO(),
        )
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(len(diagnostics.records), 4)
        self.assertEqual(
            diagnostics.selected_test_ids,
            tuple(record.test_id for record in diagnostics.records),
        )
        self.assertEqual(
            diagnostics.selection_root_sha256,
            ContinuousJob4TestDiagnosticsV1.from_dict(
                diagnostics.to_dict()
            ).selection_root_sha256,
        )

    def test_recorder_preserves_order_status_timing_and_private_hashes(self) -> None:
        test_ids = (
            "tests.test_job4_diagnostics._Fixtures.Recorder.test_pass",
            "tests.test_job4_diagnostics._Fixtures.Recorder.test_failure",
            "tests.test_job4_diagnostics._Fixtures.Recorder.test_error",
            "tests.test_job4_diagnostics._Fixtures.Recorder.test_skip",
        )
        result, diagnostics = run_job4_unittest_diagnostics(
            test_ids,
            stream=io.StringIO(),
        )
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(
            tuple(record.test_id for record in diagnostics.records),
            test_ids,
        )
        self.assertEqual(
            tuple(record.status for record in diagnostics.records),
            ("passed", "failed", "error", "skipped"),
        )
        self.assertTrue(all(record.elapsed_ns >= 0 for record in diagnostics.records))
        failure, error = diagnostics.records[1:3]
        self.assertEqual(failure.exception_type, "builtins.AssertionError")
        self.assertEqual(error.exception_type, "builtins.RuntimeError")
        self.assertTrue(failure.source_frames)
        self.assertTrue(error.source_frames)
        self.assertTrue(
            all(
                frame.relative_path == "tests/test_job4_diagnostics.py"
                for frame in (*failure.source_frames, *error.source_frames)
            )
        )
        serialized = json.dumps(diagnostics.to_dict(), sort_keys=True)
        self.assertNotIn("private failure payload", serialized)
        self.assertNotIn("private error payload", serialized)
        self.assertNotIn(str(__file__), serialized)
        self.assertEqual(
            ContinuousJob4TestDiagnosticsV1.from_dict(diagnostics.to_dict()),
            diagnostics,
        )

    def test_missing_duplicate_reordered_substituted_and_drifted_records_fail(self) -> None:
        diagnostics = ContinuousJob4TestDiagnosticsV1(
            records=(_passing_record(1, "pass"), _passing_record(2, "skip")),
            selected_test_ids=(
                _passing_record(1, "pass").test_id,
                _passing_record(2, "skip").test_id,
            ),
        )
        cases: list[tuple[str, dict[str, object]]] = []

        missing = deepcopy(diagnostics.to_dict())
        del missing["records"][0]["record_sha256"]
        cases.append(("missing", missing))

        duplicate = deepcopy(diagnostics.to_dict())
        duplicate_record = ContinuousJob4TestRecordV1(
            index=2,
            test_id=diagnostics.records[0].test_id,
            status="passed",
            elapsed_ns=2,
        )
        duplicate["records"][1] = duplicate_record.to_dict()
        cases.append(("duplicated", duplicate))

        reordered = deepcopy(diagnostics.to_dict())
        reordered["records"] = list(reversed(reordered["records"]))
        cases.append(("reordered", reordered))

        substituted_record = ContinuousJob4TestRecordV1(
            index=1,
            test_id="tests.test_job4_diagnostics._Fixtures.Recorder.test_error",
            status="passed",
            elapsed_ns=1,
        )
        substituted = deepcopy(diagnostics.to_dict())
        substituted["records"][0] = substituted_record.to_dict()
        substituted["records_root_sha256"] = canonical_sha256(
            {
                "schema_version": diagnostics.ROOT_SCHEMA_VERSION,
                "record_sha256s": [
                    item["record_sha256"] for item in substituted["records"]
                ],
            }
        )
        cases.append(("substituted", substituted))

        hash_drifted = deepcopy(diagnostics.to_dict())
        hash_drifted["records"][0]["record_sha256"] = "0" * 64
        cases.append(("record hash", hash_drifted))

        root_drifted = deepcopy(diagnostics.to_dict())
        root_drifted["records_root_sha256"] = "0" * 64
        cases.append(("root hash", root_drifted))

        selection_drifted = deepcopy(diagnostics.to_dict())
        selection_drifted["selection_root_sha256"] = "0" * 64
        cases.append(("selection root", selection_drifted))

        for label, value in cases:
            with self.subTest(label=label), self.assertRaises(ValueError):
                ContinuousJob4TestDiagnosticsV1.from_dict(value)

    def test_cycle23_bad_selector_is_rejected_and_correct_owner_resolves(self) -> None:
        bad = (
            "tests.test_pro_review_bridge.ProReviewBridgeTests."
            "test_28n_actual_scripted_v10_long_root_completes_chain_and_recovers"
        )
        corrected = (
            "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests."
            "test_28n_actual_scripted_v10_long_root_completes_chain_and_recovers"
        )
        with self.assertRaisesRegex(ValueError, "did not resolve"):
            validate_declared_unittest_ids((bad,))
        self.assertEqual(validate_declared_unittest_ids((corrected,)), {corrected: 1})

    def test_v3_result_binds_terminal_diagnostics_and_rejects_drift(self) -> None:
        manifest = {
            "cycle_id": "cycle:diagnostic-v3",
            "job4": {"task_id": "task:diagnostic-v3"},
        }
        detail = terminalized_detail(
            cycle_id=manifest["cycle_id"],
            execution_status="completed",
            execution_mode="provider_free_scripted_v10",
            provider_calls=0,
            scripted_transport_invocations=10,
        )
        diagnostics = ContinuousJob4TestDiagnosticsV1(
            records=(_passing_record(1, "pass"),),
            selected_test_ids=(_passing_record(1, "pass").test_id,),
        )
        terminal = ContinuousJob4TerminalEvidenceV6.build(
            base_terminal_evidence=decode_continuous_job4_terminal_evidence(
                detail["terminal_evidence"]
            ),
            test_diagnostics=diagnostics,
        )
        detail.update(
            {
                "terminal_evidence": terminal.to_dict(),
                "terminal_evidence_sha256": terminal.sha256,
                "test_diagnostics": diagnostics.to_dict(),
                "test_diagnostics_sha256": diagnostics.sha256,
                "test_records_root_sha256": diagnostics.records_root_sha256,
            }
        )
        value = build_canonical_job4_result(
            detail,
            task_id=manifest["job4"]["task_id"],
            report_sha256="a" * 64,
        )
        self.assertEqual(value["schema_version"], "cera.pro_review_job4_result.v3")
        self.assertEqual(validate_job4_result_contract(value, manifest), value)

        for field in (
            "test_diagnostics_sha256",
            "test_records_root_sha256",
        ):
            changed = deepcopy(value)
            changed[field] = "0" * 64
            with self.subTest(field=field), self.assertRaises(CycleError):
                validate_job4_result_contract(changed, manifest)
        changed_count = deepcopy(value)
        changed_count["test_record_count"] = 2
        with self.assertRaises(CycleError):
            validate_job4_result_contract(changed_count, manifest)

    def test_cycle23_reproduction_record_is_self_hashed_and_source_bound(self) -> None:
        message = (
            "type object 'ProReviewBridgeTests' has no attribute "
            "'test_28n_actual_scripted_v10_long_root_completes_chain_and_recovers'"
        )
        record = ContinuousJob4TestRecordV1(
            index=1,
            test_id=(
                "unittest.loader._FailedTest."
                "test_28n_actual_scripted_v10_long_root_completes_chain_and_recovers"
            ),
            status="error",
            elapsed_ns=0,
            exception_type="builtins.AttributeError",
            message_sha256=text_sha256(message),
            source_frames=(
                ContinuousJob4SourceFrameV1(
                    relative_path=(
                        ".chatgpt/pro-review/cycles/"
                        "2026-08-02-continuous-sillytavern-overnight-v3-cycle-001/"
                        "source/JOB4_AUDIT_RUNNER.py"
                    ),
                    function="TEST_TARGETS",
                    line=73,
                ),
                ContinuousJob4SourceFrameV1(
                    relative_path="tests/test_pro_review_bridge.py",
                    function=(
                        "ProReviewRepositoryCycleTests."
                        "test_28n_actual_scripted_v10_long_root_completes_chain_and_recovers"
                    ),
                    line=1089,
                ),
            ),
        )
        self.assertEqual(
            record.message_sha256,
            "1b925c54f1325c012cb37c75823f850bd69b3d036f7d3b8c93d65cc4eb2e8e62",
        )
        self.assertEqual(
            ContinuousJob4TestRecordV1.from_dict(record.to_dict()), record
        )


if __name__ == "__main__":
    unittest.main()
