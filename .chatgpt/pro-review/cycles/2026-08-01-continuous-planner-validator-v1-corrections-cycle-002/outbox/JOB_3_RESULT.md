# Progression 3 Result

task_id: `continuous-dispatch-diagnostics-and-contract-inventory-v2`

status: completed

## Outcome

Provider-call ledger v2 distinguishes prepared-not-invoked,
pretransport-failed, transport-invoked, provider completion/failure,
post-validation failure, and typed acceptance. Only an actual or conservatively
ambiguous transport invocation consumes a provider call. Known local contract
or state failures before transport remain zero-call failures.

Continuous Planner and Validator events bind the exact stored-thread hash.
Output-schema projection and request-bound MCP construction now occur before
the ledger enters provider transport. The canary harness wraps SDK import and
capability loading, active-profile inspection, session materialization, role
separation, and other pre-provider setup in named privacy-safe diagnostics.

Character Summary v2, evidence binding v2, call ledger v2, and Scene Summary
derived view v2 are versioned and registered where they are persisted as typed
records. Mutable acceptance journals remain explicitly inventoried as
dedicated operational formats. The provider-free Job 4 runner imports from any
working directory and closes SQLite before temporary cleanup.

## Verification

- adapter-level Planner, DeepSeek, and Validator pretransport/post-invocation
  accounting: passed;
- exact stored-thread hash and MCP-finalization accounting: passed;
- root-diagnostic ownership and embedded-secret redaction: passed;
- schema registry, documentation, and repository source inventory: passed;
- direct correction Job 4 runner startup from outside the repository: passed;
- focused correction tests: 79/79 passed;
- complete provider-free repository suite: 715/715 passed in 294.236 seconds,
  with one expected environment-dependent symlink-creation skip;
- compileall, active D-180 profile validation, and `git diff --check`: passed;
- active D-180 profile SHA-256 remains
  `f74347604d9adb7be0ded2a3c9c62e7076aca4d5d773aa8a3c97f8e7bcee0801`;
- provider calls and live story/database/route/service/deployment effects: 0.
