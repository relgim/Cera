# Continuous Ten-Turn Transport Blocker Result

**Date:** 2026-07-29  
**Repository:** `D:\AIChatBot\Cera`  
**Status:** persistent transport live-qualified and provider-free integrated;
continuous story goal not qualified; D-150 later removed the call-authority
blocker without changing this historical evidence

## Outcome

CERA is not yet at the manual SillyTavern human-test gate. No continuous live
route has passed ten consecutive turns, so the required clean human-test world
and port-5101 product binding were not activated.

The last two immutable routes establish a repeatable transport failure class:

- v10 passed turns 1-3, including the Mia coffee invitation that v8/v9 had
  falsely rejected, then stopped during the turn-4 Sol realization-verifier
  dispatch;
- v11 restarted from V1.2 with a materially different entry-routine route,
  passed turn 1, then stopped during the turn-2 Sol realization-verifier
  dispatch;
- successful verifier calls completed in 6.1-9.4 seconds;
- both failed verifier workers reached the exact 180-second parent timeout;
- neither failed turn created an accepted artifact, event, memory, branch-head
  update, or other story-authority write;
- v11's full worker process tree was terminated and its disposable database and
  temporary workspaces were deleted cleanly.

This supports an intermittent Codex SDK/app-server stall rather than a semantic
need for a longer verifier deadline. Increasing the deadline would preserve the
same defect while making interactive failure slower.

Immutable evidence:

- `evaluation/evidence/continuous_ten_turn_qualification_2026-07-29_v10`
- `evaluation/evidence/continuous_ten_turn_qualification_2026-07-29_v11`

The later four-call non-story qualification passed two sequential Reasoner
requests in one process and two sequential Verifier requests in a separate
process, with a fresh thread/workspace for every request. See
`PERSISTENT_CODEX_TRANSPORT_QUALIFICATION_RESULT.md` and immutable evidence
`evaluation/evidence/persistent_codex_transport_probe_2026-07-29_v1`.

## Shared corrections

The completed provider-free correction:

1. preserves active Python/domain authority while advancing the active
   DeepSeek DTO to `cera.deepseek_composition_draft.v5`;
2. replaces nullable realization ownership with one mandatory typed
   `source_unit:` or `beat:` authority ID;
3. constrains no-tool Reasoner evidence IDs to the exact request seed in the
   provider schema while preserving Python validation;
4. accepts all authoritative record-ID families required by Genesis and
   accepted continuity events;
5. treats ordinary source as verifier context without granting direct
   protected-user narration authority;
6. distinguishes NPC reaction to already-visible source from newly invented
   protected-user behavior;
7. terminalizes preparation failures and preserves prior safe receipts;
8. kills the complete Codex worker/app-server process tree after timeout;
9. records the last allow-listed worker stage on timeout and counts a dispatch
   that reached `thread_run` even when no provider receipt returns;
10. adds and live-qualifies `PersistentNoMcpCodexRunner`, which reuses
    only a supervised app-server process while creating a fresh ephemeral
    thread and isolated workspace for every request.
11. makes the continuous live runner audit the immutable call ledger before it
    creates evidence or dispatches a provider; the default 50/25 ceilings now
    reject a new route locally with the exact 10/7 remainder.

The persistent transport fixes no story semantics. Its exact four-call evidence
hash gates the prepared v12 runner. Separate instances are required for
Reasoner and Verifier, requests are serialized, and role/model/effort identity
cannot change. Request-bound MCP Reasoner calls remain isolated one-shot. Any
worker failure closes the session and fails the route without retrying that
request. V12 was not dispatched while its complete budget was unavailable.

D-150 later raised the total creator ceilings to 500 Sol and 500 DeepSeek.
V12 remains undispatched at the time of that decision, but its complete 20/10
budget is now available. The historical 50/25 accounting above remains
unchanged evidence of the earlier stop.

## Provider-free verification

The final complete suite passed:

```text
Ran 376 tests in 185.327s
OK
```

The suite includes:

- 20 sequential fake-adapter accepted/committed turns;
- real V1.2 Genesis direct, paraphrased, ambiguous, privacy-denied, stale, and
  superseded retrieval;
- branch, regeneration, restart, atomicity, replay, and SQLite integrity tests;
- provider-schema/domain differential and malformed-output tests;
- selected-participant and protected-user realization tests;
- timeout process-tree cleanup and safe stage-diagnostic tests;
- persistent-process/fresh-thread/fresh-workspace/no-MCP isolation tests;
- exact live-evidence hash and semantic-mutation activation gates;
- mixed persistent/no-MCP and one-shot/MCP story-runner selection;
- materially different v12 route and real `T-M07` paraphrase retrieval; and
- immutable continuous-live and transport-probe call-budget accounting.

The four-call live probe proves bounded process reuse but not ten-turn story
reliability, semantic quality, or human-test readiness.

## Conservative live-call ledger

`scripts/audit_continuous_live_call_budget.py` counts every provider stage that
reached a `started` stage-journal entry as one dispatch, including unreceipted
timeouts. Persistent probe calls count once their immutable evidence marks
dispatch started. Preparation failures without either marker count as zero.

```text
Sol-medium:       40 / 50 used; 10 remain
DeepSeek V4 Pro:  18 / 25 used;  7 remain
Fresh 10 turns:   20 Sol + 10 DeepSeek required
May begin:        no
```

This is intentionally conservative. It cannot under-count a dispatched request
merely because the provider failed to return a receipt.

## Advisory review

ChatGPT Pro first returned `CERA_TRANSPORT_STALL_DIAGNOSIS_ACCEPTED`, then
reviewed the completed 363-test implementation and returned
`CERA_FINAL_TRANSPORT_PROVIDER_FREE_ACCEPTED`. Pro agreed that
the evidence indicates a transport stall, that raising the timeout would hide
the problem, and that persistent process reuse is sound only when every call
uses a fresh history-free logical thread. Codex independently accepts those
points. Codex does not adopt Pro's partial 31-call estimate; the repository-wide
v1-v11 audit established the pre-probe count of 36 Sol and 18 DeepSeek
dispatches. The later passed four-call probe advances the controlling ledger to
40 Sol and 18 DeepSeek. Review of that later milestone is recorded separately.

## Exact next gate

D-150 completes the formerly required creator decision by setting both total
ceilings to 500 calls. Persistent process reuse is already qualified. The exact
next gate is now the fresh v12 route, which requires 20 Sol and 10 DeepSeek
calls and must retain one attempt per stage, immutable failure evidence, and no
retry or fallback.

No production route, persistent story world, SillyTavern activation,
publication, external handler, promotion, or deployment is implied.
