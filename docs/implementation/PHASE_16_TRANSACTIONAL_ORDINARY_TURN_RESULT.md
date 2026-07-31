# Phase 16 Transactional Ordinary-Turn Integration Result

**Date:** 2026-07-28  
**Status:** accepted by ChatGPT Pro within the provider-free ordinary transaction boundary  
**Authorization:** creator standing continuation after accepted Phase 15, recorded as D-071  
**Scope:** provider-free ordinary-route append/regeneration publication in disposable development databases

## Outcome

CERA now carries a validated ordinary result through the complete local authority boundary:

```text
LiveShapedTurnResult
-> OrdinaryTurnCommitBuilder
-> topology/source/artifact/receipt revalidation
-> TurnCommitBundle
-> SQLite prepare/finalize transaction
-> immutable source + generation + accepted artifact + receipt evidence
-> branch-head advance + CommitReceipt
```

No model can invoke this path directly. Python accepts only a matched `LiveShapedTurnResult` whose ordinary route, source ledger, request, snapshot, decision, artifact, validation receipt, lookup receipts, provider receipts, generation, branch, and lineage still agree.

## Corrected receipt persistence

The Phase 2 store atomically preserved receipt IDs inside `CommitReceipt`, but it did not preserve the corresponding Reasoner, Composer, lookup, context, and validation receipt payloads. IDs alone were insufficient for the controlling atomic receipt/audit contract.

SQLite migration 8 adds append-only `turn_receipt_records`. Each record binds:

- typed receipt ID and category;
- receipt or evidence-envelope schema version;
- canonical JSON payload;
- payload SHA-256;
- owning transaction.

`TurnCommitBundle.receipt_records` must exactly cover every declared validation, lookup, and provider receipt ID when payload records are supplied. The Phase 16 builder always supplies the complete set. The store inserts them inside the same transaction as source, generation, artifact, branch-head advance, and `CommitReceipt`. A failed finalization therefore persists none of them.

Reasoner and Composer provider records contain the normalized role receipt plus their sanitized transport/bridge receipts when present. They contain no prompt, raw source, story prose, private evidence, bearer, API key, or provider secret. Lookup records contain bounded operation/count receipts, not returned evidence. Validation records contain context assembly, optional advisory state-delta validation, Composer structural validation, and the top live-shaped turn receipt.

## Append and regeneration topology

`SceneComposerRequest` advances to `cera.scene_composer_request.v2`. It now binds an explicit publication mode and regeneration topology before prose is produced and hashed.

- Append uses the immutable snapshot head as the artifact parent.
- Regeneration must replace that current head.
- Python reads the target's stored parent and requires the proposed replacement to use that parent, producing a sibling rather than a child.
- The Composer request hash, accepted-artifact hash, live-shaped receipt, commit bundle, and commit receipt all bind the same topology.
- A branch fork created before regeneration continues to see the old sibling; the regenerated branch sees the replacement.

This avoids rewriting an already validated artifact after composition.

## Durable-state boundary

Phase 16 commits only:

- the protected source ledger reference/hash record;
- generation metadata;
- the final validated presentation-neutral accepted prose artifact;
- complete receipt evidence;
- transaction/commit receipt and branch-head transition.

It creates zero event, memory, relationship, thread, material, or development records. Reasoner state candidates remain advisory. Durable derived records continue through the separate, evidence-bound deferred consolidator after publication.

The ordinary publisher rejects consent-valid adult, blocked, and aftermath results. Adult publication is not silently routed through this path.

## Deterministic evidence

Seven new integration cases cover:

1. atomic ordinary commit plus exact replay and restart-readable receipt payloads;
2. adult-result rejection with zero writes;
3. artifact-hash tamper rejection before transaction preparation;
4. simulated failure after artifact insertion with complete rollback and explicit restart recovery;
5. optimistic stale-snapshot rejection after a competing ordinary commit;
6. post-commit fork lineage retention;
7. typed fake-transport regeneration as an immutable sibling with pre-regeneration fork isolation.

The complete repository verification result is:

```text
python -m unittest discover -s tests -p "test_*.py"
Ran 238 tests
OK (skipped=1 optional live probe)
```

`compileall` and the two documentation-validation tests pass. Actual provider/network calls: zero. All story writes occur only in auto-deleting development databases.

## Deliberate non-claims

Phase 16 does not establish:

- live Codex reasoning or DeepSeek prose quality;
- semantic/psychological quality, voice fidelity, multi-scene quality, or route promotion;
- consent-valid adult publication;
- automatic event extraction or derived-memory quality;
- production world/database binding;
- SillyTavern integration, rendering, deployment, or creator acceptance.

The accepted artifact is durable branch truth only inside the disposable test store used by this milestone. No production story data was created.

## Next safe milestone

After Pro review, the next provider-free milestone should connect a committed ordinary artifact to the already separate derived-consolidation request/validation path and presentation-neutral renderer using fake adapters and disposable databases. It must preserve the rule that publication success is not rolled back merely because deferred consolidation or rendering fails.

Adult publication, live story-role qualification, Adult EX, production binding, SillyTavern, external-handler work, promotion, and deployment remain separately closed.

## ChatGPT Pro review

ChatGPT Pro returned exactly `PHASE_16_TRANSACTIONAL_ORDINARY_TURN_ACCEPTED` with no in-scope correction. The verdict accepts the provider-free ordinary append/regeneration publication boundary, receipt-payload persistence, topology binding, zero-derived-write split, and deterministic transaction evidence only. It does not authorize adult publication, live content calls, semantic route promotion, production binding, Adult EX, SillyTavern, external-handler work, or deployment.

## Post-acceptance correction

Phase 17 found that a durable accepted artifact without a direct event record could not feed the correctly event-bound derived consolidator. D-074 therefore supersedes only Phase 16's zero-`EVENT_FACT` publication choice: the ordinary transaction now also writes one system-private direct accepted-turn event. The zero-derived-memory/relationship/thread/development rule remains unchanged. See `PHASE_17_POST_PUBLICATION_RESULT.md`.
