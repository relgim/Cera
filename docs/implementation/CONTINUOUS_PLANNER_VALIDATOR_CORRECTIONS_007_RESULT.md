# Continuous Planner/Validator Corrections 007 Result

**Decision:** D-194

**Status:** provider-free Progressions 1-3 complete; governed Stage 4 review and Job 4 not yet executed
**Active route:** unchanged `cera.active_runtime.d180.v1`

## Completed corrections

### Durable prepared ingress and closed fixtures

- `PreparedContinuousIngressBridge` accepts the exact durable raw-turn,
  prepared-turn, interpretation, and classification records and recomputes
  every authority hash before issuing a continuous receipt.
- `ContinuousIngressAuthorityStore` persists canonical immutable evidence and
  revalidates it after restart. A syntactically valid digest is never
  authority by itself.
- Qualification fixtures come from a closed repository-owned registry. An
  arbitrary `cera.fixture.*` prefix cannot mint authority, and the scripted
  executable fixture is bound to the exact module SHA-256.
- Substitution tests cover world, branch, session, request, turn,
  idempotency, raw source, protected user, adapter, span, actor, speaker,
  unknown fixture, restart, and stored-record tamper cases.

### Independent protected semantics and exact persistence targets

- The separate Validator independently adjudicates every exact Composer
  segment. Composer roles remain advisory; Python requires complete span
  coverage and rejects role/adjudication disagreement.
- Protected assertions require an exact ingress claim. Closed non-owning
  relations identify the exact NPC-owned predicate, allowing an NPC action
  toward Ted without inventing Ted's reaction.
- Explicit and pronoun-only protected action laundering through narration or
  non-owning labels fails. Valid NPC-owned affected/addressed behavior passes.
- Persistable final fields carry typed destinations with Character or
  Relationship record/subject identity, JSON path, current revision, and
  prior-value hash. Rule, Location, Event, and Scene classes remain reserved
  but disabled until their subject schemas are typed. The continuous route
  enables only exact add/replace projection; Python derives operation and
  created-field bookkeeping.
- Candidate authority now explicitly includes the independent semantic
  adjudication ledger. Accepted facts, projections, final sequences, evidence
  registry, prompts, adapters, and session policies advance compatibly.

### Actual executable provider-free qualification

- `run_continuous_planner_validator_job4.py` now has a mutually exclusive,
  exact `--confirm-provider-free-scripted-v7` mode. It cannot be selected by a
  live confirmation and requires the exact frozen fixture source hash.
- The scripted mode crosses the actual CLI identity, trigger, worktree,
  SQLite copy/integrity, root diagnostic, session construction, real stage
  ports, shared call ledger, Pro polling, ten-stage schedule, three
  acceptances, Scene Change, snapshots, archival, report/result, cleanup, and
  exit-status path.
- One real CLI subprocess proof completes with ten scripted transport
  invocations, zero external provider calls, unchanged source/disposable
  SQLite hashes, and unchanged D-180 active profile.
- The separately frozen Cycle 007 audit runner resolves 19 exact provider-free
  test identities before execution, includes that actual CLI proof, and adds
  an unchanged source/disposable SQLite assertion under the new Job 4 identity.

## Version changes

- Planner prompt/adapter: v8 / v7.
- Composer prompt/adapter/draft: v5 / v5 / v5; story segments v3.
- Validator prompt/adapter/draft: v8 / v8 / v7; final package v7.
- Final sequence v6; accepted fact v2; accepted projection v5; evidence
  registry v8.
- Session policy identities advance to
  `cera.continuous_protected_user_policy.v7` and
  `cera.continuous_session_policy.v7`.

## Verification

- Focused continuous suite: **96/96 passed** in **12.838 seconds**.
- Prepared-ingress focused suite: **6/6 passed** in **3.996 seconds**.
- Complete repository suite: **750/750 passed** in **302.051 seconds**, with
  one expected environment-dependent skip.
- Compilation, documentation validation, source inventory, active-profile
  validation, and `git diff --check`: passed.
- External provider calls, retries, fallbacks, live story writes, active-route
  changes, installed SillyTavern/service changes, deployment, merge, remote,
  and push effects: **zero**.

## Preserved limits

- Cycles 001-006 and all historical Job 4 evidence remain immutable.
- Independent Validator adjudication is still a model semantic judgment;
  Python proves exact coverage, custody, disagreement, and authority, not
  infallible natural-language understanding.
- Scripted execution proves contracts and orchestration, not live schema
  acceptance, provider session behavior, latency, prose quality, or production
  readiness. A live ten-call canary remains separately creator-gated.
