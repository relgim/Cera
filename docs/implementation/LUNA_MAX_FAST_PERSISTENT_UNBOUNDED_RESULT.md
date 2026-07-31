# Luna Max/Fast Persistent-Session Expanded-Allowance Result

**Date:** 2026-07-30  
**Status:** reasoner-only diagnostic passed 3/3  
**Route promotion:** none

## Configuration

- Model: `gpt-5.6-luna`
- Reasoning effort: `max`
- Requested service tier: `priority` (`Fast`)
- Session policy: one ephemeral branch-bound diagnostic thread reused for all turns
- Scene depth: `long`
- Diagnostic CERA output allowance: 131,072 tokens
- Retry/fallback: 0/0

The 131,072 setting is the largest value supported by CERA's diagnostic route
contract. It removes the normal 16,384-token CERA rejection threshold for this
test; it does not remove the model or provider's unavoidable limits. The active
production route was not changed.

## Results

| Turn | Provider time | Wall time | Input | Cached input | Cache ratio | Output | Reasoning output | Visible draft output |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 cold | 260.829 s | 266.119 s | 20,981 | 0 | 0.0% | 21,514 | 18,646 | 2,868 |
| 2 warm | 166.005 s | 170.438 s | 51,220 | 20,224 | 39.5% | 13,625 | 11,633 | 1,992 |
| 3 warm | 59.135 s | 63.435 s | 73,570 | 50,944 | 69.3% | 4,641 | 2,659 | 1,982 |

Relative to the cold provider turn, turn 2 was 36.4% faster and turn 3 was
77.3% faster. Total provider time was 485.969 seconds and total harness wall
time was 499.992 seconds.

All three Reasoner drafts decoded and validated as `decision_ready`:

- turn 1: three event blocks and eight event advances;
- turn 2: two event blocks and four event advances;
- turn 3: two event blocks and four event advances.

The cold turn's 21,514 output tokens explain the prior 16,384-token rejection.
Most of that usage was reasoning output, not the visible structured draft.

## Interpretation

This result supports branch-bound persistent Codex sessions as a serious CERA
latency optimization. The input context grew across turns, but a progressively
larger portion was cached, and reasoning/output usage fell sharply by turn 3.
Caching did not shrink logical context; it reduced repeated processing cost and
correlated with much lower latency.

This sequence is not a sufficient narrative-quality benchmark. Turns 2 and 3
used the same vague instruction, `Respond accordingly to Sakuras Response.`,
without supplying a new accepted visible reply. Their validated plans were
therefore nearly identical. A later qualification should use three causally
distinct turns and preserve accepted/provisional context with explicit status.

This diagnostic exercised the Reasoner only. It did not call DeepSeek or the
Sol verifier and did not test end-to-end SillyTavern latency.

## Integrity

- provider calls: 3;
- Codex threads: 1;
- story artifacts/commits: 0/0;
- SQLite integrity: `ok`;
- foreign-key findings: 0;
- stray diagnostic workers after completion: 0;
- normal development service: healthy.
