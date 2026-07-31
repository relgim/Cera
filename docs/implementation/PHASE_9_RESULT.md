# Phase 9 Provider-Free Derived Memory Result

**Date:** 2026-07-28  
**Status:** accepted; ChatGPT Pro returned `PHASE_9_ACCEPTED_PROVIDER_FREE`  
**Authorization:** D-046  
**Scope:** provider-free derived memory, relationship, thread, development, supersession, views, replay, restart, rollback, and branch isolation

## Outcome

Phase 9 implements a provider-neutral, evidence-bound derived-consolidation path. It can turn already committed validated events into typed branch-local memory, relationship, thread, or development records only after strict Python validation and one atomic SQLite commit. It can also validate an explicit no-change outcome without creating a transaction.

The implementation does not alter accepted story prose, branch head, generation, source ledger, or Genesis. It uses a separate monotonic branch `authority_revision` so two consolidations against the same story artifact cannot both silently commit.

## Implemented components

- `src/cera/consolidation/models.py`: strict request, proposal, adapter receipt, validation receipt, restart bundle, commit receipt, and derived-view contracts.
- `src/cera/consolidation/fake.py`: provider-neutral `DerivedConsolidatorPort` plus a scripted fake that is prohibited in production.
- `src/cera/consolidation/validator.py`: exact-event, owner/knowledge/privacy, protected-user, authority, record-type, supersession, development-strength, diagnosis, and Genesis boundaries.
- `src/cera/consolidation/orchestrator.py`: stable provider-free consolidation facade.
- `src/cera/storage/consolidation_store.py`: journal prepare/finalize/replay, atomic commit, restart, deterministic views, and failure-injection seam.
- SQLite migration 7: `branches.authority_revision`, `consolidation_journal`, `consolidation_receipts`, `derived_views`, indexes, and immutable journal/receipt identity triggers.
- Evidence snapshot v2: branch `authority_revision` participates in the immutable snapshot token and stale check.
- Branch-aware authority retrieval: ancestor records obey both artifact lineage and the descendant fork-creation cutoff.
- Schema registry and stable error catalog entries for all Phase 9 contracts.

## Authority rules enforced

- A proposal may create only memory, relationship, thread, or development records.
- Every record must cite exact-expanded, current, branch-visible validated event evidence.
- Owner-private evidence cannot drive another character's record.
- The protected user cannot receive inferred memories, relationship-from state, or development overlays.
- Synthetic-world fake output remains synthetic/noncanonical; disposable real-Genesis calibration requires `validated_derived` authority.
- Memory separates objective event refs, perceived facts, subjective interpretations, and unknowns; diagnosis is explicitly not assessed and Genesis effect is none.
- Relationships are directional and dimension-specific, not one universal score.
- Strong development requires repeated event evidence and cannot be permanent, global, or identity-rewriting.
- Supersession requires expansion of the exact current predecessor, matching identity/type/owner/subjects, version plus one, and a reason. History is retained.
- Generated public, owner-private, and system-private views are deterministic, hash-bound, scoped, and non-authoritative.

## Transaction and recovery evidence

- A prepared bundle is durable and restartable without repeating the consolidator call.
- Failure after record insertion rolls back records, receipt, and revision advance while leaving the prepared journal available.
- Exact replay returns the stored commit receipt.
- Competing prepared bundles are serialized by `authority_revision`; the loser fails without partial state.
- Derived commit leaves accepted artifact and story generation unchanged.
- View rebuilding is separate from canonical commit and reconstructs from current branch-visible records.
- A fork cannot see a later parent consolidation at the same story artifact; parent and sibling branches cannot see child-local consolidation.

## Validation

Focused Phase 9 tests:

```text
python -m unittest tests.test_consolidation -v
Ran 10 tests
OK
```

The focused suite covers complete commit/replay/view/privacy behavior, stale snapshots, exact-dossier tamper rejection, invalid authority/diagnosis/Genesis/protected-user/event rules, private-owner separation, supersession, same-head fork isolation, atomic failure and restart, competing transactions, explicit no change, and disposable real-Hanezawa-Genesis derived-memory retrieval.

Complete repository suite:

```text
python -m unittest discover -s tests -v
Ran 169 tests in 42.488s
OK
```

The final post-verdict run also passed `compileall` and documentation validation with zero findings.

## Scope exclusions

This result does not implement or qualify:

- live Codex or any other consolidation provider;
- live DeepSeek or prose-quality behavior;
- Adult EX import;
- external-handler inspection, attachment, prompting, or testing;
- a production story database or production-world binding;
- SillyTavern integration or deployment;
- Phase 10 evaluation, provider qualification, or production acceptance.

The real-Genesis test uses an auto-deleting disposable development database and makes zero provider or network calls.

## ChatGPT Pro review

ChatGPT Pro returned `PHASE_9_ACCEPTED_PROVIDER_FREE`. It found the authority division, exact-evidence revalidation, typed derived models, supersession, separate authority revision, atomicity/restart behavior, fork cutoff, and non-authoritative view design coherent within the supplied provider-free evidence packet. No concrete in-scope correction was required.

The review was advisory rather than a direct repository audit. It does not qualify live Codex or DeepSeek, psychological/prose quality, provider transport, production databases/world binding, Adult EX, an external handler, SillyTavern, deployment, Genesis promotion, or Phase 10. Separate creator authorization remains required.
