# Continuous corrections v5 provider-free Job 4

- Cycle: `2026-08-01-continuous-planner-validator-v1-corrections-cycle-005`
- Task: `continuous-corrections-v5-provider-free-integration-audit`
- Status: `failed`
- Direct execution attempts: `1`
- Unique unittest methods: `13`
- Labeled assertions: `14`
- Provider calls: `0`
- Started UTC: `2026-08-01T16:03:02.255478+00:00`
- Elapsed seconds: `1.214`

## Labeled assertions

1. **passed** - Explicit ingress ownership replaces text heuristics (`tests.test_continuous_corrections.ContinuousAuthorityV5Tests.test_ingress_owned_source_units_replace_text_heuristics`)
2. **passed** - Continuous ingress source coverage is gap-free (`tests.test_continuous_corrections.ContinuousAuthorityV5Tests.test_continuous_request_rejects_unclassified_source_gaps`)
3. **passed** - Composer exact claims and realization segments agree (`tests.test_continuous_corrections.ContinuousAuthorityV5Tests.test_composer_must_annotate_every_exact_protected_source_occurrence`)
4. **passed** - Composer paraphrased and hidden Ted ownership fails (`tests.test_continuous_corrections.ContinuousAuthorityV5Tests.test_composer_rejects_paraphrased_or_hidden_ted_ownership`)
5. **passed** - Final, event, and edit cannot extend a valid Ted claim (`tests.test_continuous_corrections.ContinuousAuthorityV5Tests.test_final_event_and_edit_cannot_extend_one_valid_ted_claim`)
6. **passed** - Candidate authority manifest is immutable (`tests.test_continuous_corrections.ContinuousWorldHardeningTests.test_candidate_authority_manifest_is_required_and_immutable`)
7. **passed** - Final actors, subjects, segments, and edits remain traceable (`tests.test_continuous_corrections.ContinuousEvidenceCorrectionTests.test_final_sequence_and_edits_retain_beat_traceability`)
8. **passed** - Public, owner-private, and ownerless authority projections fail closed (`tests.test_continuous_corrections.ContinuousAuthorityV5Tests.test_accepted_projection_rejects_public_or_cross_owner_private_state`)
9. **failed** - Pre-v5 continuous session compatibility cannot resume (`tests.test_continuous_planner_validator.ContinuousSessionLifecycleTests.test_restart_rejects_pre_v5_policy_compatibility`)
10. **passed** - New canary identity is parameterized and historical identity is rejected (`tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_parameterized_canary_identity_rejects_historical_reuse`)
11. **passed** - Exact JobHarness completes all ten fake stages (`tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_exact_job_harness_completes_all_ten_provider_free_stages`)
12. **passed** - Actual subprocess sidecar marks thread_run failure (`tests.test_continuous_job4_harness.ContinuousJob4HarnessTests.test_actual_subprocess_sidecar_marks_post_submit_failure`)
13. **passed** - D-180 active profile remains unchanged (`tests.test_active_runtime_profile.ActiveRuntimeProfileTests.test_all_active_source_bindings_match_the_canonical_profile`)
14. **passed** - Source and disposable SQLite hashes unchanged (`provider_free_read_only_sqlite_check`)

## SQLite evidence

```json
{
  "disposable_sha256_after": "bfbf23eb2fa7199fad38e8fb3f1ae0d6b547467f17ed68e84b7115275a2fc555",
  "disposable_sha256_before": "bfbf23eb2fa7199fad38e8fb3f1ae0d6b547467f17ed68e84b7115275a2fc555",
  "disposable_unchanged": true,
  "foreign_key_findings": 0,
  "integrity_check": "ok",
  "source_sha256_after": "bfbf23eb2fa7199fad38e8fb3f1ae0d6b547467f17ed68e84b7115275a2fc555",
  "source_sha256_before": "bfbf23eb2fa7199fad38e8fb3f1ae0d6b547467f17ed68e84b7115275a2fc555",
  "source_unchanged": true
}
```

The runner selected the corrected ingress, final-candidate, accepted-context, authority-identity, canary, and subprocess-observer tests directly. All provider boundaries used scripted fakes or pre-provider sentinels. No live provider transport was constructed. No active route, story state, installed SillyTavern, deployment, remote, merge, or push effect occurred.
