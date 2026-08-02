# Continuous Lean Context Short Canary V2 Specification

**Decision lineage:** D-200 plus D-201

**Status:** frozen non-dispatching specification for a later separately identity-bound task

**Active route during this specification:** unchanged `cera.active_runtime.d180.v1`

## Purpose

Measure same-thread Planner input reduction, latency, causal continuity, and
scene quality under the V2 closed packet contracts. This document grants no
provider call or publication authority.

## Fixed route and budget

- Planner: `gpt-5.6-sol`, reasoning effort `medium`, Fast disabled.
- Composer: `deepseek-v4-flash`, thinking disabled and unchanged behavior.
- Validator: the separately qualified continuous Validator route.
- First-attempt mode: `lean_continuous`.
- Maximum external calls: ten for one attempt.
- Retry, fallback, provider substitution, hidden repair, Detailer, and extra
  verifier: zero.
- No in-attempt context-mode switch.

## Ten-call schedule

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

Turn 1 must use the closed first-turn packet on a fresh compatible physical
Planner thread. After disposable acceptance, Python injects the exact accepted
envelope and value-free stable descriptors once.

Turn 2 must use the same physical Planner thread and the closed ordinary lean
packet. It must omit Turn 1's user/accepted pair, complete sequence, accepted
fact values, prose, full history, stable instruction duplicate, and unchanged
summary. It retains only the compact accepted head, stable keys, current source
and ingress custody, request evidence handles, and any newly selected changed
summary.

Scene Change must use the closed hash-bound derived handoff and exclude exact
prior pairs. Python and Validator retain exact accepted-pair custody. A provider
fork or reconstruction is an explicit boundary: a successful accepted-checkpoint
fork must create a distinct child thread and child keys; a lost, archived,
incompatible, deliberate-restart, or non-forkable case must reconstruct a new
thread. Neither operation is an ordinary-turn mode switch.

## Required evidence

For every stage record:

- packet schema, kind, canonical bytes, SHA-256, and replay equality;
- physical thread, root/candidate, world, branch, scene, request, and turn;
- base, logical-component, actual submitted, injected, reconstructed, and
  provider-reported token counts when exposed;
- cached, uncached, total input, output, reasoning, first-result, and total
  latency when exposed;
- tool calls/failures and actual provider attempts;
- summary-delivery and accepted-injection receipts;
- cited-only Validator accepted-evidence closure;
- fork/reconstruction initialization and reference-transfer custody;
- terminal effects, capability boundary, archive, transaction, publication,
  completion, and recovery custody.

Compare Turn 1 and Turn 2 packet/submitted bytes, provider tokens, and latency.
Do not attribute changes to caching without separately authorized control
evidence.

## Stop rules

Stop the attempt without retry on any packet-field or mode mismatch, prohibited
Turn 2 component, stale/foreign/unbound reference, missing summary or ingress
custody, missing cited Validator value, Composer prior projection, thread drift
outside an explicit initialization, provider/schema/transport failure, or
nonzero excluded effect. Preserve evidence and return to provider-free diagnosis
under a new identity.

## Exclusions

This specification authorizes no provider dispatch, live publication,
production/default activation, live-story acceptance, story/production-database
write, active-route change, SillyTavern/service change, deployment, merge,
remote operation, push, retry, fallback, hidden repair, provider substitution,
Detailer, extra verifier, or automatic False Positive.
