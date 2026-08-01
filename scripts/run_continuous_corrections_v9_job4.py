"""Provider-free integration audit for continuous correction cycle 009."""

from __future__ import annotations

import scripts.run_continuous_corrections_v7_job4 as audit


audit.CYCLE_ID = (
    "2026-08-01-continuous-planner-validator-v1-corrections-cycle-009"
)
audit.TASK_ID = "continuous-corrections-v9-provider-free-result-contract-audit"
audit.AUDIT_VERSION = "v9"
audit.CASES = (
    (1, "Live and scripted terminal results share the strict canonical DTO", "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_live_and_scripted_terminal_results_match_strict_cycle_schema"),
    (2, "Legacy canary-only fields remain rejected by the strict decoder", "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_strict_cycle_schema_rejects_legacy_canary_result_fields"),
    (3, "The actual scripted-V8 CLI emits the aligned canonical result", "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_actual_cli_completes_closed_provider_free_scripted_v8_mode"),
    (4, "A completed live-shaped result crosses complete-job4", "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests.test_28a_completed_live_shaped_canary_result_completes_job4"),
    (5, "A failed live-shaped result crosses complete-job4", "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests.test_28b_failed_live_shaped_canary_result_completes_job4"),
    (6, "A failed scripted result crosses complete-job4", "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests.test_28c_failed_scripted_canary_result_completes_job4"),
    (7, "The actual scripted-V8 canary crosses publication through complete-job4", "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests.test_28d_actual_scripted_v8_canary_completes_job4_transport"),
    (8, "complete-job4 rejects both legacy unknown-field variants", "tests.test_pro_review_bridge.ProReviewRepositoryCycleTests.test_28e_complete_job4_rejects_legacy_canary_fields"),
    (9, "Repository source inventory includes every current runtime source", "tests.test_structural_contract_v2.StructuralV2FailureAndInventoryTests.test_repository_source_inventory_includes_runtime_and_v2_packages"),
    (10, "Controlling documentation remains complete and linked", "tests.test_documentation.DocumentationTests.test_authoritative_documentation_is_complete_and_linked"),
    (11, "D-180 active runtime identity remains unchanged", "tests.test_active_runtime_profile.ActiveRuntimeProfileTests.test_all_active_source_bindings_match_the_canonical_profile"),
)


if __name__ == "__main__":
    raise SystemExit(audit.main())
