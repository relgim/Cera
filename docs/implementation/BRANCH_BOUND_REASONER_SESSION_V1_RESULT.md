# Branch-Bound Reasoner Session Architecture V1 Result

**Date:** 2026-07-30  
**Decision:** D-172  
**Status:** provider-free implementation complete; live benchmark not authorized  
**Active route:** unchanged Sol-medium fresh-thread development route

## Implemented boundary

CERA now has an inactive provider-neutral branch-session layer under
`src/cera/reasoner_session/`:

- `ReasonerSessionPort` for create, candidate fork, branch fork, accepted
  receipt injection, rejection marking, invalidation, and resume probing;
- `ReasonerSessionCompatibility` hashing every route and authority dimension
  that can make existing provider context unsafe to reuse;
- durable `ReasonerSessionLedger` and accepted/candidate/rejected/invalidated
  `ReasonerSessionCheckpoint` records;
- `ContextAuthorityDelta` and complete `SessionReconstructionBundle` contracts;
- content-addressed accepted-turn and rejected-candidate receipts;
- exact creator feedback constraints, branch-local by default and global only
  through explicit scope;
- compatible rotation, incompatible invalidation/reconstruction, provider-loss
  recovery, candidate failure invalidation, regeneration, and CERA branch fork;
- deterministic exact-evidence materialization reuse restricted to accepted
  ancestry with unchanged version, hash, and sections;
- byte-equivalent separation of current Reasoner stable instructions and
  variable canonical turn packet;
- privacy-safe session usage projection with exact cache/uncached arithmetic
  and explicit null/unknown unsupported metrics;
- `InMemoryReasonerSessionPort`, which performs zero provider calls.

SQLite migration 16 adds session, checkpoint, constraint, accepted/rejected
receipt, and usage-receipt tables with identity/terminal immutability, no-delete
triggers, one active session per branch/role, and one open candidate per
session.

## Creator-review behavior

The active creator-review coordinator and SillyTavern route were not rewired.
The new session coordinator consumes the existing durable review records:

- candidate/review request and branch bindings must match;
- acceptance is impossible before the existing atomic story commit exists;
- acceptance injects a hash-bound receipt with zero provider calls/retries;
- rejection leaves the accepted session pointer unchanged;
- a bare decline creates no inferred rule;
- explicit correction diagnostics become exact creator constraints;
- raw rejected prose remains purged under the existing resolved-review rule.

This preserves creator publication authority without allowing creator review to
override identity, privacy, character knowledge, branch, supersession,
consent/capacity, participant, protected-user, evidence, schema, or atomic
publication validation.

## Regeneration and branches

A normal candidate forks from the current accepted checkpoint. A regeneration
candidate instead forks from the checkpoint for the replaced artifact's
parent, while its Python delta still binds the current head and generation.
Acceptance therefore produces a true sibling lineage. Rejection leaves the
replaced artifact active.

A CERA branch fork verifies the child's parent branch and exact fork artifact,
then creates a separate compatibility/session identity. Branch-local creator
constraints and rejected children are not selected into the sibling.

## Provider-free evidence

Focused `tests.test_reasoner_session_architecture` covers:

- accepted-checkpoint candidate forks;
- creator acceptance after commit and failure before commit;
- rejected isolation and exact feedback constraints;
- bare decline, branch/global scope, and constraint supersession;
- candidate failure, provider-loss restart, prompt-version invalidation, and
  rotation blocking with an open candidate;
- regeneration and CERA branch fork;
- rejected-evidence ancestry attacks and current/revoked overlap;
- evidence materialization mutation checks;
- prompt split byte equivalence and evidence preservation;
- safe usage receipts, cache arithmetic, unsupported metrics, and privacy;
- durable decode after SQLite reopen;
- compatibility-hash mutation coverage.

The focused architecture suite passes 23/23.

## Complete verification

- focused architecture suite: 23/23 passed;
- impacted integration suite: 60/60 passed in 16.083 seconds;
- final complete repository suite: 546/546 passed in 257.427 seconds;
- final documentation/foundation regression: 31/31 passed in 2.085 seconds;
- `python -m compileall -q src tests`: clean;
- `python -m cera.documentation .`: clean with zero findings.

The first repository-wide run executed 530 tests and exposed one circular
import between storage, provider evaluation, and the eager
`reasoner_session` package exports. The package now exposes prompt and
observability helpers lazily. A fresh-process import probe passed, followed by
the 31-test focused regression and the complete 546-test clean run. The
failure was not hidden or counted as a pass.

The machine-readable evidence summary, source hashes, and negative claims are
recorded in
`evaluation/evidence/branch_bound_reasoner_session_v1_provider_free_2026-07-30/summary.json`.

## Explicit non-claims

- No provider was called.
- No benchmark was run.
- No active prompt, route, model, provider worker, or SillyTavern behavior was changed.
- No story database or production world was created or modified.
- No raw rejected prose was retained.
- No Adult ON/EX publication, route promotion, retry/fallback, deployment, or
  persistent non-ephemeral restart storage was activated.

## Next gate

After provider-free closure, the creator may separately authorize the matched
Sol-medium fresh-session versus branch-session live benchmark defined in
`SOL_MEDIUM_SESSION_ARCHITECTURE_REVIEW_AND_DECISION.md`. D-172 itself grants
no live-call authority.
