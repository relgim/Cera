# Continuous Short Canary V11 Specification

**State:** frozen provider-free publication-readiness contract; it is not a
provider-dispatch authorization. Live-canary-002 remains gated by an accepted
Cycle 011 response and a new identity-bound Stage B publication progression.

## Preserved route and schedule

- Planner: `gpt-5.6-sol`, medium effort, Fast disabled.
- Composer: `deepseek-v4-flash`, thinking disabled.
- Validator: `gpt-5.6-terra`, high effort, Fast disabled.
- Three disposable turns across two scenes and exactly ten provider
  dispatches: Planner, Composer, Validator for Turns 1 and 2; one Validator
  scene summary; then Planner, Composer, Validator for Turn 3.
- The messages, summary scoping, accepted-context rules, no-retry rule, and
  stop conditions remain byte-for-byte governed by the V10 fixture contract.

## Required new identity and terminal ownership

- Live-canary-001 and every failed or completed earlier Job 4 identity remain
  immutable. Live-canary-002 requires a new checkpoint, cycle, task,
  authorization, call ledger, transaction, runtime root, and evidence set.
- The one-shot root terminal transaction begins before every fallible setup
  operation. A started identity cannot repeat setup, semantic, or provider
  work. Frozen bytes may only be republished; a committed identity refuses
  rerun.
- Canonical result v2 binds exact immutable
  `JOB4_TERMINAL_EVIDENCE.json` bytes. Repository completion, copied artifacts,
  completion receipt, completed-chain validation, and recovery must all decode
  and reconcile the same terminal status and canonical effects.

## Capability and archival custody

- All live-story, production-database, active-route, deployment, remote,
  merge, push, service, and installed-SillyTavern mutation capabilities are
  absent or represented by sealed counted ports. Canonical operational effects
  derive only from this capability ledger.
- Planner and Validator use the same closed terminal archival contract in live
  and scripted modes. Each role records a hashed thread identity and archive
  reason, archive-request outcome, post-archive resume outcome, supported
  backend active-selection outcome, and local accepted-ancestry invalidation.
- Request success alone is insufficient. Resume success, active/accepted
  selectability, an unknown verification outcome, or a verification error
  forces terminal failure.

## Required pre-dispatch gate

Before any later live publication, the exact source line must retain provider-
free proof for the complete actual-CLI failure matrix: source/hash/copy/SQLite,
integrity/foreign keys, ledger, world, lifecycle, profile, SDK/account/backend,
session/thread, archive request and verification, accepted synchronization and
injection, every terminal serialization/projection/write stage, and the commit-
marker cut point.

Every case must produce or recover one stable failed result/report and immutable
terminal artifact, preserve exact capability-derived effects, pass real
`complete-job4`, recover to `response_pending`, perform no retry, and refuse a
committed identity. The successful scripted ten-stage path must also pass the
same CLI, transaction, completion, receipt, completed-chain, and recovery path
with ten local invocations and zero external calls.

## Remaining live risks and stop boundary

Provider-free qualification cannot establish provider schema acceptance,
semantic or prose quality, live thread continuity, latency, tokens, or account
behavior. Only the separately gated live-canary-002 can measure those facts.
This specification does not authorize a provider call, production/default
activation, live-story acceptance, live database mutation, SillyTavern or
service alteration, deployment, merge, remote operation, push, retry,
fallback, hidden repair, provider substitution, extra verifier, Detailer, or
automatic False Positive.
