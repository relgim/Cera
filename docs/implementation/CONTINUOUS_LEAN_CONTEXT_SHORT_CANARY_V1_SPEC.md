# Continuous Lean Context Short Canary V1 Specification

**Decision:** D-200  
**Status:** frozen design for a later separately identity-bound task; not publication or dispatch authority  
**Active route during this specification:** unchanged `cera.active_runtime.d180.v1`

## Purpose

Measure operational Planner prompt reduction, latency, continuity, and scene quality under `lean_continuous` without weakening any current authority or using an in-attempt fallback.

## Exact route

- Planner: `gpt-5.6-sol`, reasoning effort `medium`, Fast disabled.
- Composer: `deepseek-v4-flash`, thinking disabled.
- Validator: the separately qualified continuous Validator route.
- Retry/fallback/provider substitution/hidden repair/Detailer/extra verifier: `0`.
- First attempt context mode: `lean_continuous` only.

## Exact ten-call schedule

```text
1. Turn 1 Planner
2. Turn 1 Composer
3. Turn 1 Validator
4. Turn 2 Planner
5. Turn 2 Composer
6. Turn 2 Validator
7. Scene 1 Validator summary
8. Turn 3 Planner
9. Turn 3 Composer
10. Turn 3 Validator
```

Turn 1 uses a fresh compatible Planner thread, one-time base instructions, the bounded first-turn packet, and the required Sakura summary. After disposable acceptance, the exact accepted envelope and stable descriptors inject once.

Turn 2 uses the same physical Planner thread and must omit stable instructions, Turn 1's accepted pair, Turn 1's complete sequence, accepted fact payloads, and unchanged Sakura summary. It carries the compact accepted-head receipt and stable keys.

Scene Change is explicit. Turn 3 may include Mia's current incomplete summary because the primary character changes. The Planner receives a lean derived scene handoff without prior exact-pair replay; Python and Validator retain exact accepted-pair custody.

## Acceptance and stop rules

Disposable acceptance is allowed only under the separately published canary identity after every existing review, protected-user, evidence, privacy, persistence, and atomic-publication gate passes.

Stop the attempt on:

- any context-mode change;
- a missing/duplicated/unsynchronized accepted injection;
- any prohibited Turn 2 component;
- an unknown/stale/foreign/sibling accepted reference;
- missing current Composer character context;
- incomplete Validator closure;
- thread identity drift outside explicit initialization;
- provider/schema/transport failure;
- any retry, fallback, excluded effect, or nonzero unapproved operation.

Do not switch to `projection_assisted` and do not reconstruct inside the live attempt. Preserve evidence and diagnose provider-free under a new identity.

## Required measurements

For every stage, retain physical-thread identity, exact context mode, logical component sizes, actual submitted text sizes when known, provider cached/uncached/total input when exposed, output/reasoning usage, first-result/total latency when exposed, tool calls/failures, rich-sequence depth/validation, accepted injection bytes/receipt, and all terminal/effect custody.

Report Turn 1-to-Turn 2 prompt and latency changes. Do not attribute a difference to hidden cache behavior without a separately authorized control.

## Exclusions

This specification authorizes no provider call, live publication, fresh-thread control, twenty-turn run, production/default activation, live story/database effect, installed SillyTavern/service change, deployment, merge, remote, or push. A later task must bind its own checkpoint, cycle, exact provider budget, authorization record, and creator approval.
