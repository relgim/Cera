# Phase 18 Durable Post-Publication Work Result

**Date:** 2026-07-28  
**Status:** accepted by ChatGPT Pro  
**Authorization:** standing provider-free continuation after Phase 17 acceptance  
**Scope:** atomic work scheduling, durable result/no-change evidence, restart recovery, and explicit retry control in disposable development databases

## Outcome

CERA no longer depends on the process remaining alive after accepted story publication:

```text
atomic story transaction
-> accepted source/prose/direct event/receipts/branch head
-> pending render work
-> pending consolidation work
-> pending derived-view work (depends on consolidation)

explicit dispatcher
-> claim one work item
-> run exactly one requested consumer
-> durable completed | no_change | failed
```

SQLite migration 10 adds `post_publication_work` plus `post_publication_attempts`. The requested work identities and canonical request payloads are inserted in the same transaction as the accepted artifact. A story rollback therefore leaves neither story truth nor orphan work. A process exit immediately after commit leaves restart-readable pending work.

## Work contracts

`TurnCommitBundle` advances its hash domain to `cera.turn_commit_bundle.v2` and may carry one immutable `PostPublicationWorkRequest` for each of:

- pure rendering, bound to the accepted-artifact hash and renderer profile;
- derived consolidation, bound to the accepted-artifact hash, direct event, protected-user identity, and world mode;
- derived-view rebuilding, bound to and dependent on that consolidation work item.

The bundle validates that every work item belongs to the same branch/artifact, preserves the exact accepted-artifact hash, schedules no duplicate kind, binds consolidation to the direct event inserted by the same transaction, and keeps the derived-view dependency inside the same bundle.

## Durable lifecycle and retry rule

```text
pending -> running -> completed
                   -> no_change
                   -> failed

failed -> running only with:
          exact failed work ID
          + non-empty authorization reason
```

There is no background retry, fallback provider, recursive repair, or automatic replay of a failed call. `PostPublicationCoordinator.resume(...)` dispatches pending work, but it leaves failed work failed unless the caller names that exact work ID and supplies an authorization reason.

If a process stops with work marked `running`, explicit restart recovery converts it to a sanitized `failed` record with `manual_after_review`. It is not automatically called again because a provider may have completed externally even when the local completion write was lost.

Each attempt retains its immutable identity, authorization-reason SHA-256, start, terminal outcome, and result/error hash. The raw operator rationale is not stored. Durable errors store only stage, exception type, a stable operator message, retry mode, and any recovery-reason hash; they do not store provider response bodies, prompts, evidence, prose, or secrets.

## Idempotent completion

- Pure render results are durable non-authoritative projections and are reconstructed from the journal on replay.
- A committed consolidation is rediscovered by its artifact-derived request ID. If the process stopped after the derived commit but before work completion, an explicitly authorized retry links the existing commit without invoking the consolidator again.
- A validated consolidation no-change result now durably preserves lookup, consolidator, and validation receipt evidence in the work result. Replay does not call the consolidator again.
- Derived-view work is a separate dependency. A consolidation no-change deterministically completes it as `no_change`; a committed consolidation permits a deterministic rebuild. A failed dependency leaves it pending.

## Deterministic evidence

Seven focused end-to-end cases now cover:

1. publication, rendering, and durable no-change consolidation;
2. event-backed private memory, derived commit, views, privacy, replay, and restart;
3. simultaneous renderer/consolidator failure without story rollback;
4. process exit immediately after publication followed by restart dispatch;
5. exact no-change replay without a second consolidator invocation;
6. failed work held across restart until exact-ID/reason authorization;
7. interrupted-running recovery plus atomic rollback of scheduled work with the story transaction.

The complete repository suite passes:

```text
python -m unittest discover -s tests -p "test_*.py"
Ran 245 tests
OK (skipped=1 optional live probe)
```

`compileall` and documentation validation pass. Actual provider/network calls: zero. All story, work, and consolidation writes use auto-deleting development databases.

## Deliberate non-claims

Phase 18 does not establish:

- a background scheduler, multi-process lease policy, backpressure, or operational UI;
- exactly-once behavior from an external provider that ignores CERA idempotency identity;
- live renderer, consolidator, Reasoner, or Composer quality/reliability;
- adult publication or adult-memory behavior;
- production-world/database binding or production recovery policy;
- SillyTavern integration, Adult EX, external-handler work, route promotion, or deployment.

## Next safe milestone

After Pro review, the next bounded provider-free milestone should add an operator-facing runtime service boundary for listing pending/failed work, performing explicit recovery/retry commands, and returning stable error envelopes without exposing story evidence or provider details. It may use fake adapters and disposable databases only. Live content calls, adult publication, production binding, SillyTavern, Adult EX, external-handler work, promotion, and deployment remain separately closed.

## ChatGPT Pro review

ChatGPT Pro returned exactly `PHASE_18_DURABLE_POST_PUBLICATION_ACCEPTED` with no in-scope correction. The verdict accepts only the provider-free durable-work scheduling, recovery, no-change, and explicit-retry milestone. It does not authorize live content calls, adult publication, semantic promotion, production binding, Adult EX, SillyTavern, external-handler work, or deployment.
