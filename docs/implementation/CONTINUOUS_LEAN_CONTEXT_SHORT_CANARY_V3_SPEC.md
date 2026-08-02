# Continuous Lean Context Short Canary V3 Specification

**Decision lineage:** D-200 plus D-201 and the provider-free V3 custody corrections

**Status:** frozen non-dispatching specification for a later separately identity-bound task

**Active route during this specification:** unchanged `cera.active_runtime.d180.v1`

## Purpose

Measure same-thread Planner context reduction, latency, causal continuity, and
scene quality only after the V3 branch-materialization and total-thread-lineage
contracts have been independently reviewed. This document grants no provider
publication, dispatch, production activation, or creator-acceptance authority.

## Fixed route and governed budget

- Planner: `gpt-5.6-sol`, reasoning effort `medium`, Fast disabled.
- Composer: `deepseek-v4-flash`, thinking disabled and unchanged behavior.
- Validator: the separately qualified continuous Validator route.
- First-attempt Planner mode: `lean_continuous`.
- Exactly ten external calls maximum in one separately authorized attempt.
- Twenty external calls maximum across at most two separately reviewed attempts.
- Each attempt requires its own repository-bound identity and authorization.
- Retry, fallback, provider substitution, hidden repair, Detailer, extra
  verifier, automatic False Positive, and in-attempt context-mode switching:
  zero.

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

Turn 1 uses the closed first-turn packet on a fresh compatible physical
Planner thread. After disposable acceptance, Python injects the exact accepted
envelope and the value-free stable descriptors exactly once.

Turn 2 uses the same physical Planner thread and the closed ordinary lean
packet. It omits Turn 1's user/accepted pair, complete sequence, accepted fact
values, prose, full history, duplicated stable instructions, and unchanged
summary. It retains only compact accepted-head custody, stable keys, current
source and ingress custody, request evidence handles, and any newly selected
changed summary.

Scene Change uses the closed hash-bound derived handoff and excludes exact
prior pairs. Python and Validator retain exact accepted-pair custody. A
provider fork or reconstruction is an explicit physical-thread boundary, not
an ordinary-turn context-mode switch.

## V3 branch-materialization boundary

Before an accepted-checkpoint provider fork or cross-branch reconstruction,
Python must validate one immutable materialization receipt binding:

- the exact accepted checkpoint, complete ancestry, accepted head, and cutoff;
- actual parent and child world-directory identities;
- complete parent and child `ACTIVE` manifests and tree hashes;
- every Character, Relationship, Rule, Location, Event, Scene, world index,
  accepted promotion artifact, and `WORLD_STATE.json` identity;
- current scene, ordered accepted-turn IDs, and world-state revision;
- parent provider thread plus authority, privacy, protected-user, session, and
  persistence policy identities;
- every inherited character-summary source path, revision, bytes, owner,
  authority class, and derived envelope.

An empty, partial, stale, foreign, replayed, mismatched, or intentionally
divergent child fails before physical fork or reconstruction transport. A
valid child receives child-local stable-reference keys, exact-once value-free
descriptor transfer, child snapshot persistence, and revalidated summary
delivery. Parent and sibling keys remain unusable.

## V3 total-thread boundary

Every physical Planner and Validator thread touched by the attempt enters one
closed immutable lineage ledger. This includes initial, resumed, forked,
reconstructed, auxiliary, adopted, superseded, abandoned, failed, final, and
Validator threads.

Each thread must finish in exactly one condition:

1. the explicitly authorized active handle; or
2. verified archival with resume failure, backend non-selectability, and
   coordinator non-selectability.

Any post-creation failure must archive and verify the child before returning.
Unknown, duplicate, orphaned, contradictory, unresolved, resumable, selectable,
or unarchived threads make the attempt terminally failed. Terminal evidence V5
binds the complete lineage. Restart and recovery may publish only frozen bytes
and may not repeat semantic or provider work.

## Required measurement evidence

For every stage record:

- packet schema, kind, canonical bytes, SHA-256, and replay equality;
- physical thread, root/candidate, parent, world, branch, scene, request, and
  turn identities;
- base, logical-component, actual submitted, injected, reconstructed, and
  provider-reported token counts when exposed;
- cached, uncached, total input, output, reasoning, first-result, and total
  latency when exposed;
- tool calls, tool failures, finish status, and actual provider attempts;
- same-thread Turn 1 versus Turn 2 packet bytes, submitted bytes, prompt-token
  omission, cache usage, and latency;
- summary-delivery, accepted-injection, stable-reference, materialization,
  child-snapshot, and initialization receipts;
- cited-only Validator accepted-evidence closure and semantic-match result;
- complete thread-lineage transitions and terminal dispositions;
- terminal effects, nine-port capability boundary, root transaction, archive,
  immutable publication, completion, completed-chain, and recovery custody.

Do not attribute latency or token changes to provider caching without a
separately authorized control. Provider conversation state is not durable story
memory.

## Stop rules

Stop the attempt without retry on any packet-field or mode mismatch, prohibited
Turn 2 component, stale/foreign/unbound reference, missing summary or ingress
custody, invalid materialization receipt, child snapshot divergence, missing
cited Validator value, semantic mismatch, Composer prior projection, thread
drift outside an explicit initialization, unclosed physical thread, provider or
schema failure, transport failure, or nonzero excluded effect. Preserve the
available evidence and return to provider-free diagnosis under a new identity.

## Exclusions

This specification authorizes no provider publication or dispatch, fresh-thread
control, twenty-turn qualification, production/default activation, live-story
acceptance, story or production-database write, active-route change,
SillyTavern or service change, deployment, merge, remote operation, push,
retry, fallback, hidden repair, provider substitution, Detailer, extra
verifier, Fast mode, automatic False Positive, or creator-authority expansion.
