# Phase 4 Result — Snapshot Evidence Service and Turn Kernel

**Status:** accepted by Pro  
**Date:** 2026-07-28  
**Scope:** provider-free Python infrastructure using temporary synthetic worlds only

```text
real_creator_genesis_available = false
real_creator_genesis_approved = false
canonical_character_records_installed = false
synthetic_fixture_mode = true
live_provider_calls_made = false
production_story_database_created = false
```

## Outcome

Phase 4 now supplies the deterministic runtime boundary that was missing between the authority store and future model adapters.

Implemented evidence capabilities:

- immutable per-turn snapshot tokens bound to request, world, branch, local generation, exact branch head, Genesis revision, access perspective, visibility policy version, and real-versus-synthetic mode;
- explicit stale-token rejection after branch-head, generation, policy, token, world, or Genesis mismatch;
- separate `search_evidence`, `fetch_evidence`, `get_character_sections`, and `get_continuity` operations;
- compact search references that do not return the exact claim used for ranking;
- exact fetch only for explicitly requested, authorized records and sections;
- privacy, knowledge-owner, content-class, validity, branch-lineage, supersession, world-mode, and Genesis-revision filtering before return;
- privacy revalidation on exact fetch and every linked continuity step;
- complete metadata preserving authority, epistemic class, truth status, owner, knowledge owners, visibility, source references, validity, branch origin, and supersession state;
- deterministic limits for query size, search count, result count, fetch count, traversal depth, response bytes, and cumulative per-turn evidence bytes;
- typed truncation receipts or fail-closed limit errors;
- rebuildable FTS5 indexing whose rows never establish truth or access; exact authoritative records are reloaded and authorized before return;
- exact index validation before search, with typed failure rather than an unfiltered scan when the index is stale or corrupt;
- read-only lookup behavior with zero authority-store writes.

Implemented Turn Kernel capabilities:

- protected-source intake identities and hashes;
- optimistic generation, parent-artifact, and immutable Genesis checks;
- protected-user and present-cast validation;
- ordinary and consent-valid-adult preflight routes;
- snapshot-authorized confirmed-adult identity evidence plus typed current consent, capacity, pressure, and freedom-to-stop gates;
- first-boundary blocker rejection for an already classified non-consensual crossing;
- stable error envelopes with no story commit and no fallback;
- provider-free prepared-turn output containing the pending source-ledger record and evidence lookup receipts;
- advisory state-delta validation that rejects Genesis rewrites, cross-branch deltas, unauthorized evidence, absent-character state, and model-inferred protected-user state.

Python consumes typed normalized authority facts. It does not classify consent by keyword substitution or rewrite user intent to make a route pass.

## Branch and index behavior

Child branches retrieve only the immutable artifact ancestry visible from their fork head. Origin-branch generation intervals are not incorrectly compared to a child's restarted local generation counter. A child supersession hides its inherited target only in that child lineage; the parent and siblings retain their own active truth.

The FTS5 index is derived. Rebuild reads the append-only Genesis and branch authority records. Every search first proves that the complete index exactly matches authoritative hashes. A corrupt candidate therefore produces `CERA_EVIDENCE_INDEX_INVALID`; it never widens access or silently falls back to an authority-table scan.

## Verification

Repository validation passed:

- Python bytecode compilation: pass;
- deterministic unit tests: **62/62 pass**;
- documentation validation: pass with zero findings.

Phase 4 adds 24 focused tests covering:

- perspective-dependent privacy and exact-fetch reauthorization;
- compact search versus exact expansion;
- linked-record authorization;
- stale snapshots;
- branch inheritance, fork isolation, and child-local supersession;
- superseded audit fetch;
- belief, feeling, and unresolved epistemic labels;
- cross-world and Genesis-revision rejection;
- deterministic byte, count, depth, and follow-up budgets;
- index rebuild equivalence and corruption failure;
- synthetic rejection by real-world mode;
- restart replay and request-local budget isolation;
- explicit evidence-service outage;
- read-only evidence operations;
- ordinary, adult-authority, blocker, stale-request, cast, protected-user, state-delta, and no-mutation Turn Kernel behavior.

All database and Genesis records used by tests were temporary noncanonical Alpha/Beta fixtures. No fixture database remains in the repository.

## Deliberately not done

- no creator Genesis was supplied, compiled, or installed;
- no Hana or other real character record was created;
- no provider transport, runtime Codex call, DeepSeek call, or fallback was added;
- no real story database, Adult EX material, SillyTavern route, external handler, deployment, or credential was created;
- no raw source-text persistence or provider packet was implemented;
- no evidence index rebuild was coupled to story publication yet; future publication orchestration must rebuild or transactionally refresh the derived index before another search.

## Review request

Pro returned `CONTINUE_TO_PHASE_5_FAKE_REASONER` and accepted the deferred publication/index coupling because stale index state fails closed.
