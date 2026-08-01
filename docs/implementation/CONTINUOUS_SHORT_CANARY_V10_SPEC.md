# Continuous Short Canary V10 Specification

**State:** conditionally authorized by the creator's continuous queue; dispatch
remains closed until the accepted Cycle 010 provider-free gate and a new
identity-bound Stage B publication progression

## Preserved live route

- Planner: `gpt-5.6-sol`, medium effort, Fast disabled.
- Composer: `deepseek-v4-flash`, thinking disabled.
- Validator: `gpt-5.6-terra`, high effort, Fast disabled.
- Three disposable turns across two scenes, exactly ten provider dispatches.
- One attempt per stage; no retry, fallback, hidden repair, provider
  substitution, extra verifier, Detailer, eleventh call, or automatic False
  Positive.

## Required new identity

- Live-canary-001 and checkpoint
  `918b006f25ab638a7328287832796976158cdd3b` remain immutable failure evidence.
- The first later attempt uses separately frozen live-canary-002 checkpoint,
  cycle, task, authorization, call-ledger, and evidence identities. A failed
  identity is never reused.
- Its source snapshot must descend from an accepted Cycle 010 response and bind
  the exact active queue revision.

## Exact fixture and schedule

The ten calls remain:

1. Turn 1 Planner, Composer, Validator.
2. Turn 2 Planner, Composer, Validator.
3. Scene 1 Validator summary.
4. Turn 3 Planner, Composer, Validator.

The exact messages are:

1. `Hello, my name is Ted. Is this the Hanezawa residence?`
2. `I'm the tenant who was supposed to arrive today.`
3. `Several days later, Ted is in the kitchen with Mia and asks, "Is Sakura always that cautious with visitors?"`

Turn 1 supplies only the required incomplete revision-bound Sakura summary.
Turn 2 uses accepted Turn 1 authority and continuous context without resending
that summary. Before Turn 3, the same Validator thread summarizes only accepted
Turns 1 and 2 and must exclude the held Turn 3 prompt. Turn 3 supplies only the
required incomplete revision-bound Mia summary and preserves Ted's exact
supplied action and dialogue.

## Terminal truth and effect custody

- The harness-owned `cera.continuous_job4_terminal_evidence.v1` record is the
  only source for terminal status and the four canonical effects.
- Provider calls come from the hard ledger. Route effects come from exact
  before/after active-profile inspection. Story/database and prohibited
  operation effects come from explicit counters bound with database,
  integrity, archival, accepted-session, injection, and synchronization checks.
- Missing, malformed, contradictory, or unverified evidence fails before result
  publication. Every submitted provider operation counts even if later stages
  fail.
- A mandatory postcondition failure forces top-level `failed`; it cannot publish
  a completed zero-effect receipt.

## Disposable acceptance and stop rule

Automatic acceptance is available only inside the disposable canary after all
existing semantic, evidence, protected-user, privacy, owner, branch,
record-policy, atomic-promotion, thread-injection, immutable-snapshot, and
synchronization gates pass with semantic status `accepted`, severity `good`,
and `accept_allowed`.

Any concern, critical, rejection, provider, schema, domain, tool, summary,
persistence, synchronization, archival, result-contract, or isolation failure
stops before the next call and preserves evidence. There is no in-attempt retry.

## Remaining live risks

Provider-free validation cannot establish live schema acceptance, semantic
accuracy, prose quality, provider thread continuity, latency, or token behavior.
Those properties require the conditionally authorized live-canary-002 after its
accepted predecessor and provider-free publication gate.
