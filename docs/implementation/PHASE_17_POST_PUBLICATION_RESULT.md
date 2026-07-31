# Phase 17 Provider-Free Post-Publication Result

**Date:** 2026-07-28  
**Status:** accepted by ChatGPT Pro  
**Authorization:** standing provider-free continuation after Phase 16 acceptance  
**Scope:** direct accepted-turn event evidence, pure rendering, and deferred consolidation with fake adapters in disposable databases

## Outcome

CERA now connects ordinary publication to its two downstream consumers without giving either consumer power to undo publication:

```text
validated ordinary LiveShapedTurnResult
-> atomic story publication + direct accepted-turn event
-> independently:
   -> pure presentation renderer
   -> exact event fetch -> DerivedConsolidatorPort -> Python validation
      -> derived-only transaction -> regenerated views/index
```

Publication is the first durable boundary. Rendering and consolidation run afterward in independent failure domains. A render failure, consolidator outage, validation failure, derived-transaction failure, or view rebuild failure cannot delete or roll back the accepted source, prose artifact, direct event, generation, branch head, or story commit receipt.

## Correction to the Phase 16 handoff

During Phase 17 inspection, Codex found that Phase 16 committed durable prose but no objective event evidence. The existing consolidator is correctly forbidden from inventing an event, so it could not create an evidence-backed memory from a normal accepted turn. This made the apparent post-publication handoff incomplete even though the Phase 16 transaction itself was sound.

Phase 17 corrects that design. `OrdinaryTurnCommitBuilder` now creates exactly one `accepted_turn_event` `EvidenceDocument` in the same transaction as the source, generation, accepted prose, receipt evidence, and branch head.

The record contains only:

- validated realized current-segment beat IDs, actors, states, neutral event descriptions, and cited evidence IDs;
- source-unit coverage declarations;
- accepted artifact, prose hash, decision, generation, source, and source-hash bindings;
- the validated stop boundary and participant identities.

It does not contain a model-authored memory, relationship state, diagnosis, Genesis rewrite, future conditional segment, or unvalidated advisory state candidate. It is `system_private` and carries explicit knowledge owners, so later owner-specific derived records must still be proposed and validated. Regeneration places the replacement event on the replacement sibling; the old event remains on the old sibling and is not visible on the regenerated branch.

## Atomic search projection

Adding a direct event record initially caused the next turn's voice-card search to fail closed because the FTS projection was stale. The correction is at the owning write boundary: both story and consolidation transactions now insert each new authoritative `EvidenceDocument` row into the rebuildable FTS projection inside the same SQLite transaction as the authority record.

A failed transaction rolls back both authority and search rows. Regeneration and branch visibility remain enforced by the authoritative record set; FTS supplies candidates only and cannot grant visibility.

## Post-publication coordinator

`PostPublicationCoordinator`:

1. atomically commits the ordinary result;
2. renders the committed accepted artifact through `PresentationRendererPort`, if requested;
3. opens a fresh post-commit immutable snapshot;
4. exact-fetches the direct event without filesystem or index authority;
5. builds `cera.derived_consolidation_request.v2` with the actual lookup receipt payload;
6. calls one requested `DerivedConsolidatorPort` with no retry or fallback;
7. stages the proposal through the existing strict `ConsolidationValidator`;
8. commits only memory, relationship, thread, or development records when justified;
9. rebuilds derived views after a successful derived commit;
10. reports each downstream status independently.

A committed consolidation is keyed to the artifact-derived request ID. Replaying the same post-publication operation discovers the existing consolidation receipt, does not call the consolidator again, and does not duplicate records.

## Consolidation audit persistence

`DerivedConsolidationRequest` and `ConsolidationBundle` advance to v2. SQLite migration 9 adds append-only `consolidation_receipt_records`. A full Phase 17 consolidation transaction stores:

- exact-fetch lookup receipt;
- provider-neutral consolidation reasoner receipt;
- Python consolidation-validation receipt;
- derived authority records and consolidation commit receipt.

The receipt records contain hashes, IDs, counts, and typed declarations rather than prompts, exact evidence text, accepted prose, or secrets.

## Deterministic evidence

Three new end-to-end cases cover:

1. committed ordinary publication, pure rendering, and validated no-change consolidation;
2. direct accepted-turn event -> Hana-owned private memory -> derived commit -> receipt payloads -> views/index -> owner-only retrieval -> exact replay -> restart;
3. simultaneous renderer failure and consolidator unavailability while the story artifact/event remain committed and authority revision remains unchanged.

Existing tests additionally cover consolidation tamper, invalid authority, diagnosis/Genesis/protected-user/event rejection, supersession, branch/fork privacy, derived-transaction rollback/restart, and competing authority revisions. The complete suite passes:

```text
python -m unittest discover -s tests -p "test_*.py"
Ran 241 tests
OK (skipped=1 optional live probe)
```

`compileall` and the two documentation-validation tests pass. Actual provider/network calls: zero. All story and derived writes use auto-deleting development databases.

## Deliberate non-claims

Phase 17 does not establish:

- live consolidator, Reasoner, or Composer behavior;
- semantic quality of event summaries or derived memories;
- consent-valid adult publication or adult memory behavior;
- production scheduling, queue durability, backpressure, or operational monitoring;
- SillyTavern rendering/integration;
- production world/database binding, route promotion, deployment, or creator acceptance.

No Adult EX, external handler, production story data, or live content call was used.

## Next safe milestone

After Pro review, the next provider-free milestone should add a durable post-publication work journal/outbox so render and consolidation requests survive process termination between story commit and downstream completion, including explicit no-change completion. That must preserve publication-first semantics, idempotency, no fallback, and independent retries only after manual or scheduler policy authorization.

Adult publication, live role qualification, production binding, SillyTavern, Adult EX, external-handler work, promotion, and deployment remain separately closed.

## ChatGPT Pro review

ChatGPT Pro returned exactly `PHASE_17_POST_PUBLICATION_ACCEPTED` with no in-scope correction. The verdict accepts the provider-free direct-event, atomic-search-projection, pure-rendering, and deferred-consolidation milestone only. It does not authorize live content calls, adult publication, semantic promotion, production binding, Adult EX, SillyTavern, external-handler work, or deployment.
