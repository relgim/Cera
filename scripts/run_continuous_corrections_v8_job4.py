"""Provider-free integration audit for continuous correction cycle 008."""

from __future__ import annotations

import scripts.run_continuous_corrections_v7_job4 as audit


audit.CYCLE_ID = (
    "2026-08-01-continuous-planner-validator-v1-corrections-cycle-008"
)
audit.TASK_ID = "continuous-corrections-v8-provider-free-integration-audit"
audit.AUDIT_VERSION = "v8"
audit.CASES = (
    (1, "Raw ingress reaches a resolved continuous request through the real shadow boundary", "tests.test_structural_contract_v2.StructuralV2IngressTests.test_sillytavern_shadow_request_uses_resolved_prepared_receipt"),
    (2, "The prepared classifier registry rejects unknown and substituted implementations", "tests.test_structural_contract_v2.StructuralV2IngressTests.test_prepared_classifier_registry_rejects_unknown_and_substituted_types"),
    (3, "Prepared authority rejects adapter, descriptor, identity, owner, and span substitutions", "tests.test_structural_contract_v2.StructuralV2IngressTests.test_prepared_ingress_authority_rejects_identity_and_span_substitutions"),
    (4, "The exact frozen-fixture registry remains a distinct closed authority path", "tests.test_structural_contract_v2.StructuralV2IngressTests.test_continuous_fixture_prefix_is_not_authority"),
    (5, "Character and relationship metadata and nested-path escapes are immutable", "tests.test_continuous_world.ContinuousWorldTests.test_v8_persistence_policy_rejects_metadata_and_pointer_escapes"),
    (6, "A real but unrelated relationship target is rejected", "tests.test_continuous_world.ContinuousWorldTests.test_v8_relationship_target_must_be_justified_by_final_field_roles"),
    (7, "Post-edit validation rejects a JSON-valid but record-invalid candidate", "tests.test_continuous_world.ContinuousWorldTests.test_v8_post_edit_validation_rejects_json_valid_record_invalidity"),
    (8, "Approved Character and Relationship add operations remain atomic", "tests.test_continuous_world.ContinuousWorldTests.test_atomic_multi_file_apply_updates_all_records_together"),
    (9, "An approved Character replace remains atomic under the V8 policy", "tests.test_continuous_world.ContinuousWorldTests.test_v8_approved_character_replace_remains_atomic"),
    (10, "Pre-V8 classifier and persistence compatibility fails closed on restart", "tests.test_continuous_planner_validator.ContinuousSessionTests.test_restart_rejects_pre_v8_classifier_and_write_policy_compatibility"),
    (11, "Candidate identity changes with ingress and persistence policy custody", "tests.test_continuous_world.ContinuousWorldTests.test_provider_free_turn_coordinator_prepares_then_accepts_once"),
    (12, "Independent semantics rejects explicit and pronoun protected assertions without claims", "tests.test_continuous_corrections.ContinuousEvidenceCorrectionTests.test_independent_semantics_rejects_explicit_and_pronoun_laundering"),
    (13, "Missing, conflicting, incomplete, and unknown semantic adjudications fail", "tests.test_continuous_corrections.ContinuousEvidenceCorrectionTests.test_independent_semantics_rejects_missing_conflicting_and_incomplete_adjudication"),
    (14, "A valid NPC action toward Ted remains non-owning", "tests.test_continuous_corrections.ContinuousEvidenceCorrectionTests.test_independent_semantics_allows_npc_action_toward_ted_without_reaction"),
    (15, "The actual Job 4 CLI crosses prepared and fixture paths in scripted V8 mode", "tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_actual_cli_completes_closed_provider_free_scripted_v8_mode"),
    (16, "Three turns across two scenes preserve separate sessions and accepted projections", "tests.test_continuous_corrections.ContinuousProviderFreeIntegrationTests.test_three_turn_two_scene_flow_keeps_separate_sessions_and_appends_once"),
    (17, "Scene Change excludes the new prompt and expires prior-scene projections", "tests.test_continuous_world.ContinuousWorldTests.test_scene_change_uses_allow_list_tail_and_excludes_new_prompt"),
    (18, "Good assessment uses ordinary atomic Accept", "tests.test_continuous_world.ContinuousWorldTests.test_candidate_isolation_and_atomic_accept"),
    (19, "Eligible Concern uses audited False Positive", "tests.test_continuous_world.ContinuousWorldTests.test_false_positive_accepts_eligible_concern_and_records_diagnostic"),
    (20, "Eligible Critical uses the same bounded False Positive transaction", "tests.test_continuous_corrections.ContinuousWorldHardeningTests.test_critical_false_positive_is_eligible_only_with_concern_semantics"),
    (21, "Repository source inventory includes every current runtime source", "tests.test_structural_contract_v2.StructuralV2FailureAndInventoryTests.test_repository_source_inventory_includes_runtime_and_v2_packages"),
    (22, "Controlling documentation remains complete and linked", "tests.test_documentation.DocumentationTests.test_authoritative_documentation_is_complete_and_linked"),
    (23, "D-180 active runtime identity remains unchanged", "tests.test_active_runtime_profile.ActiveRuntimeProfileTests.test_all_active_source_bindings_match_the_canonical_profile"),
)


if __name__ == "__main__":
    raise SystemExit(audit.main())
