# Branch-Bound Reasoner Live Five-Run Result

**Decision:** D-173  
**Date:** 2026-07-31  
**Status:** terminal at 1/5; provider-adapter correction and rerun not authorized  
**Active route:** unchanged

## Outcome

The controlled live diagnostic did not establish a working provider-side
candidate-fork chain.

| Turn | Provider dispatch | Result | Time |
|---|---:|---|---:|
| 1, cold root | Sol-medium | Passed Reasoner v24 decoding and validation | 112.566 s |
| 2 | none | `thread/fork`: no rollout found for ephemeral source | 0.025 s |
| 3 | none | same pre-dispatch failure | 0.023 s |
| 4 | none | same pre-dispatch failure | 0.025 s |
| 5 | none | same pre-dispatch failure | 0.025 s |

Total live usage was one Sol-medium call, zero DeepSeek calls, zero retries,
zero fallbacks, and zero story-authority writes. The active route and
SillyTavern were unchanged. The disposable database retained zero artifacts,
reported `integrity_check=ok`, and had zero foreign-key findings.

## Successful cold-turn evidence

The accepted structural result was `decision_ready`, cited four exact evidence
items, and contained three causally distinct event blocks:

1. Sakura confirms the household and formally controls the threshold.
2. Hana joins for a causally supported authority-level welcome.
3. Sakura resumes practical control and leaves the next meaningful choice to
   Ted without speaking or acting for him.

The plan used Sakura and Hana, had no insufficiency, and stopped at a meaningful
protected-user decision. It is good bounded evidence for the current Reasoner
contract, not evidence for multi-turn caching or branch-session reliability.

Usage was 22,055 input tokens, zero cached input, 5,061 output tokens, and
2,238 reasoning-output tokens. The full stable prompt prefix was moved
byte-identically into thread base instructions; the variable packet remained
the canonical current-turn authority.

## Root cause

The provider-free V1 design assumed that app-server could create a provider
candidate by forking the accepted ephemeral thread. That assumption is false
for the installed Codex SDK/app-server behavior.

The first harness version attempted to fork an empty ephemeral root and stopped
five times before dispatch with zero provider calls. V2 correctly ran the cold
turn on the root first. `thread/fork` still failed afterward with:

```text
JSON-RPC error -32600: no rollout found for thread id ...
```

The installed SDK schema defines an ephemeral thread as one that should not be
materialized on disk. App-server's fork operation resolves rollout history,
so it cannot fork this ephemeral source. This is a transport-capability mismatch,
not an API quota error, cache failure, Reasoner semantic failure, or Python
validation failure.

## Codex assessment

Silently changing the benchmark to non-ephemeral threads would contradict the
creator's decision to defer restart-persistent rollout retention until CERA is
working without bugs. It would also answer a different architectural question.

The recommended correction is an ephemeral-compatible live adapter:

- append candidates run linearly on the current accepted ephemeral thread;
- a successful candidate remains provisional until creator review and Python
  commit, with no later provider turn allowed while review is unresolved;
- acceptance continues that thread and supplies the hash-bound accepted receipt
  in the next variable packet with zero extra provider call;
- rejection, provider failure, or invalid output discards the contaminated
  ephemeral thread and reconstructs a clean thread from Python's accepted
  checkpoint before any later turn;
- regeneration and CERA branch forks always start a clean reconstructed thread
  from the selected accepted Python checkpoint;
- Python's existing compatibility, privacy, evidence, branch, review, and
  publication validators remain final authority.

This preserves rejected-candidate isolation and ephemeral operation. Its tradeoff
is that rejection, regeneration, and branch forks lose provider cache/context
and must reconstruct from authoritative Python records. That is preferable to
retaining noncanonical context or enabling persistent rollout storage prematurely.

The alternative is to reopen non-ephemeral provider threads, which would allow
literal `thread/fork` but requires a separate retention, cleanup, restart,
privacy, and archive policy. Codex does not recommend that as the immediate fix.

## Evidence

- `evaluation/evidence/branch_bound_reasoner_live_five_2026-07-31_v1/summary.json`
  preserves the zero-call empty-root fork failure.
- `evaluation/evidence/branch_bound_reasoner_live_five_2026-07-31_v2/summary.json`
  preserves the terminal one-call, 1/5 result.

No raw provider output, prompt, private evidence, or secret is retained in
either summary.

## Next gate

A new creator authorization is required to implement the provider-free
ephemeral-compatible adapter correction. Only after its state-machine,
rejection, reconstruction, restart, regeneration, branch, privacy, and mutation
tests pass should another five-turn live qualification be requested.
