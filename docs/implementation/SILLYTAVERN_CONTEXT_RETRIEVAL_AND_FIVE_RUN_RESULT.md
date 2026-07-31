# SillyTavern context, retrieval, and five-run result

**Decision:** D-166  
**Date:** 2026-07-30  
**Repository:** `D:\AIChatBot\Cera`

## Outcome

CERA completed five accepted real SillyTavern turns on the persistent
non-production Hanezawa branch. The client used Sol-medium as Scene Reasoner,
DeepSeek V4 Flash with thinking as Scene Composer, and Sol-medium as independent
Scene Realization Verifier. Every provider stage was one-shot; no retry or
fallback was enabled.

The five accepted turns were:

| Case | Depth | Result |
|---|---|---|
| Sakura returns while Ted waits; Hana joins the floor | Auto | Accepted after creator review; the initial concern exposed missing accepted-context visibility in the verifier |
| Ted requests tea before the orientation | Auto | GOOD; accepted |
| Ted asks what matters before orientation | Auto | GOOD; accepted |
| Sakura performs the household orientation | Medium | GOOD after exact public-rule retrieval and closed-world Composer correction; accepted |
| Ted asks Sakura to prioritize one first-week rule | Epic | GOOD under the evidence-aware verifier; accepted |

The actual SillyTavern **Regenerate** command was also exercised at Epic depth.
It reached the CERA sibling-generation route without the previously reported API
error. Its first provisional candidate was deliberately declined because it
restated protected source action and added unsupported household defaults. That
diagnostic was not counted as an accepted turn and made no story-state commit.

## Shared corrections

- Auto now requires supported causal follow-through beyond the first answer or
  reaction when a materially different consequence remains available.
- The last four accepted presentation-neutral replies are supplied as bounded
  verifier continuity while regeneration excludes the head being replaced.
- Orientation and household-information cues deterministically seed the two
  public authoritative household-rule records. Search results are never treated
  as exact evidence.
- Runtime Codex is instructed to retrieve exact world facts and to preserve
  owner-private and character-knowledge boundaries when citing them.
- DeepSeek's active modular authority profile treats protected-user predicates
  and concrete world facts as closed-world. It forbids plausible converse,
  inverse, default, exception, or implementation details not stated by evidence.
- The independent Sol verifier now receives a bounded verifier-only projection
  of the exact evidence already selected for the Composer. It must report any
  unsupported concrete world claim in the creator review instead of marking the
  prose GOOD from sequence coverage alone.
- Provider-facing bookkeeping remains minimal: Python derives IDs, hashes,
  citation bindings, owner identity, and source coverage authority.

## Active versions

- Reasoner adapter/prompt: `v23` / `v23`
- Reasoner evidence MCP contract: `v6`
- Composer adapter/prompt: `v28` / `v26`
- Composer packet: `v15`
- Runtime modular Composer registry: `v7`, authority module `v6`
- DeepSeek route adapter: `v24`, model `deepseek-v4-flash` with thinking
- Scene Realization Verifier adapter/prompt: `v8` / `v8`
- Scene Realization Verification Request: `v7`
- SillyTavern response profile: `cera-sillytavern-auto-v6`

## Validation

- Focused Composer/provider contracts: 63/63 passed.
- Evidence-aware verifier and live-shaped pipeline group: 89/89 passed.
- SillyTavern adapter group: 14/14 passed.
- Complete provider-free suite: **501/501 passed in 493.712 seconds**.
- The loopback development service is healthy on port 5101.

## Remaining limits

- Live ordinary turns still take roughly three to four minutes, with Epic near
  the upper end. The Reasoner/provider chain is the main latency concern.
- Five accepted turns establish bounded functionality, not universal prose or
  character-quality reliability. Creator review remains required.
- Live Adult ON/EX publication, production binding, promotion, deployment, and
  an external handler remain outside this gate.
- SillyTavern Vector Storage was disabled during this test because its unrelated
  collection-query errors cluttered diagnosis; CERA retrieval does not depend on
  that extension.
