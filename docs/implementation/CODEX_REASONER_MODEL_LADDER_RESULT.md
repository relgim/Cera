# Codex Reasoner Model Ladder Result

**Date:** 2026-07-30 (America/Vancouver)  
**Authority:** D-167  
**Terminal evidence:** `codex-reasoner-model-ladder-2026-07-30-v3`  
**Result:** 4/16 complete pipelines accepted; no route promotion

## Executive result

Changing the Codex Reasoner model by itself does not fix the current CERA
latency or reliability problem.

- All 16 Reasoner calls returned a structurally valid `decision_ready` outcome.
- Only 4/16 complete Reasoner -> Composer -> Verifier pipelines passed.
- Eight turns ended because DeepSeek Flash did not return one complete
  candidate, one ended on a DeepSeek atomic-obligation failure, and three
  complete candidates were rejected by the Sol verifier for unsupported
  protected-user realization.
- No HTTP, fetch, URL, credential, or provider-transport API error occurred in
  the terminal v3 batch. The earlier browser error `TypeError: Failed to fetch`
  was not reproduced.
- The current Sol-medium route remains unchanged. This small, single-cue test
  is insufficient evidence for promotion.

The best latency candidate was Terra-low. The most promising conservative
shortcut was Sol-low. Terra-medium obtained 2/2 mechanical pipeline passes,
but human review found one incorrect floor-owner selection and doorway/phone
continuity drift, so its apparent win is not a quality win.

## Test design

The ladder used the same real V1.2 prepared turn for every run:

```text
Depth: Auto
User source: Hello, Hanezawa residence?
```

The prepared request contained 14 seed records and a 24,716-byte seed dossier.
The authoritative story-start context says Sakura handles the formal arrival.
Every candidate received two identical semantic requests:

1. sample 1, with a new route-specific CERA prompt namespace;
2. sample 2, with the identical request and namespace.

This makes the CERA-owned request cold then repeatable. It cannot clear or
control provider-side platform baseline caches, so sample 1 may still report
cached tokens unrelated to the newly namespaced CERA payload.

The matrix was:

1. Luna high
2. Luna xhigh
3. Terra low
4. Terra medium
5. Terra high
6. Terra xhigh
7. Sol low
8. Sol medium

Every run used the current DeepSeek V4 Flash thinking Composer and the current
Sol-medium realization verifier. There was one attempt per stage, no retry, no
fallback, no story commit, and no production binding.

## Quantitative comparison

`Full pass` includes Composer and Verifier, so it is not a pure Reasoner score.
Reasoner latency is the cleanest model-to-model comparison.

| Reasoner | Full pass | Avg Reasoner | Range | Input tokens | Cached input | Output tokens | Reasoning tokens | Blocks / advances |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Luna high | 0/2 | 126.1 s | 115.8-136.3 s | 56,022 | 43,520 | 11,721 | 7,686 | 2/6, 2/4 |
| Luna xhigh | 0/2 | 202.4 s | 181.4-223.4 s | 54,045 | 31,488 | 20,363 | 15,969 | 2/6, 2/6 |
| Terra low | 1/2 | 41.3 s | 38.5-44.1 s | 41,686 | 0 | 4,044 | 1,032 | 2/4, 1/3 |
| Terra medium | 2/2 | 53.1 s | 51.9-54.4 s | 41,690 | 0 | 5,329 | 2,136 | 2/4, 2/4 |
| Terra high | 0/2 | 89.9 s | 78.1-101.7 s | 55,629 | 30,464 | 8,459 | 4,561 | 2/4, 2/4 |
| Terra xhigh | 0/2 | 92.9 s | 92.8-93.0 s | 41,694 | 5,632 | 9,664 | 6,258 | 2/4, 2/4 |
| Sol low | 1/2 | 77.9 s | 75.8-80.0 s | 41,684 | 0 | 5,234 | 1,260 | 2/5, 2/5 |
| Sol medium | 0/2 | 102.8 s | 95.2-110.4 s | 41,684 | 0 | 7,210 | 3,232 | 2/5, 2/7 |

Across the terminal batch, Reasoner calls consumed 374,134 input tokens,
111,104 reported cached-input tokens, 72,024 output tokens, and 42,134
reasoning tokens. Codex calls were ChatGPT-quota metered and recorded zero API
currency cost; those zeroes do not mean zero quota use.

## Cold and repeated-request observations

| Reasoner | Sample 1 cache | Sample 2 cache | Sample 1 time | Sample 2 time | Finding |
|---|---:|---:|---:|---:|---|
| Luna high | 9,984 | 33,536 | 115.8 s | 136.3 s | More reported cache, but slower |
| Luna xhigh | 31,488 | 0 | 223.4 s | 181.4 s | Cache was not stable |
| Terra low | 0 | 0 | 44.1 s | 38.5 s | Faster repeat without reported cache |
| Terra medium | 0 | 0 | 54.4 s | 51.9 s | Small repeat improvement |
| Terra high | 0 | 30,464 | 78.1 s | 101.7 s | More reported cache, but slower |
| Terra xhigh | 2,816 | 2,816 | 92.8 s | 93.0 s | Effectively unchanged |
| Sol low | 0 | 0 | 75.8 s | 80.0 s | Repeat slightly slower |
| Sol medium | 0 | 0 | 110.4 s | 95.2 s | Repeat faster without reported cache |

The provider request hash was identical inside every pair. Provider-side cache
use is opportunistic, not guaranteed, and was not a reliable latency predictor
in this batch. CERA should not promise a faster second response merely because
the semantic prompt is repeated.

## Time by pipeline stage

Only seven Composer calls produced a complete candidate with a usable provider
receipt. Their observed stage durations were:

| Sample | Reasoner | Composer | Verifier | Total | Result |
|---|---:|---:|---:|---:|---|
| Luna xhigh 2 | 178.6 s | 81.3 s | 30.2 s | 294.4 s | verifier rejected |
| Terra low 2 | 36.2 s | 62.2 s | 11.7 s | 113.8 s | passed |
| Terra medium 1 | 51.9 s | 27.3 s | 10.2 s | 93.3 s | passed |
| Terra medium 2 | 49.2 s | 69.9 s | 20.5 s | 143.8 s | passed |
| Terra xhigh 2 | 90.7 s | 8.1 s | 28.9 s | 131.4 s | verifier rejected |
| Sol low 1 | 73.5 s | 60.5 s | 24.9 s | 163.0 s | verifier rejected |
| Sol low 2 | 77.6 s | 36.0 s | 12.5 s | 130.1 s | passed |

The seven receipted DeepSeek candidates cost an estimated total of $0.014906.
The other nine Composer attempts have no retained failure-side token/cost
receipt, so the exact total DeepSeek spend cannot be reconstructed. That is an
observability defect, not evidence that those calls were free.

## Reasoning, logic, and story assessment

### Shared strength

Every model respected the protected-user stop boundary and produced typed,
evidence-cited decisions. Sol-low and Sol-medium consistently chose Sakura,
kept the arrival provisional, and did not invent Ted's reply.

### Shared weakness

Nearly every model reduced Auto to the same narrow chain:

```text
acknowledge the residence
-> request identity or purpose
-> wait for Ted
```

The plans were valid but too conservative for the creator's desired Auto
experience. None created the richer, causally supported household domino or
meaningful secondary development expected from this prompt. Higher effort
mostly increased time and reasoning tokens rather than broadening the usable
scene.

### Important model findings

- **Luna high/xhigh:** not a speed solution. Luna high was slower than
  Sol-medium on average; Luna xhigh was the slowest and most token-intensive
  route. Its longest realized candidate was also repetitive.
- **Terra low:** fastest by a clear margin, but one plan collapsed to one event
  block and three advances. It is useful as a latency baseline, not yet a
  quality default.
- **Terra medium:** fastest route with 2/2 mechanical passes, but sample 2 chose
  Hana even though authoritative starting context assigns the formal arrival
  to Sakura. Its realized prose also drifted into telephone language such as
  `calling` rather than consistently maintaining the doorway/doorbell scene.
  The current verifier accepted both errors. This route must not be promoted
  from its raw pass rate.
- **Terra high/xhigh:** offered no demonstrated quality or reliability return
  for their extra latency and tokens.
- **Sol low:** about 24% faster than Sol-medium in Reasoner latency and retained
  consistent Sakura selection. It is the best conservative comparison
  candidate, though its prose remained short and one verifier rejection shows
  that the full route is not yet reliable.
- **Sol medium:** produced the strongest maximum causal-advance count in one
  sample and consistently selected Sakura, but still ended at the same basic
  identity-request boundary. It did not justify its extra latency on this cue.

## API and failure diagnosis

The terminal v3 batch did not reproduce an API transport failure. Its 12
failures were application-level fail-closed outcomes:

| Failure class | Count | Owner |
|---|---:|---|
| DeepSeek did not return one complete candidate | 8 | Composer transport/completion contract |
| DeepSeek atomic structured obligation failed | 1 | Composer output/validation contract |
| Unsupported protected-user realization | 3 | Composer realization plus final verifier |

The eight incomplete completions are the largest immediate reliability issue.
The current failure path also discards the exact DeepSeek finish reason and
usage receipt, preventing a clean distinction among output truncation, missing
structured content, and another terminal completion state. A user-facing CERA
turn would still show an error for these cases even though it is not a network
API error.

## Technical assessment and next work

No model should be promoted from this batch. Keep the current Sol-medium route
until a separately governed correction and broader comparison.

The next work should address shared architecture before spending more model
calls:

1. Preserve a privacy-safe DeepSeek failure receipt containing finish reason,
   duration, token usage, estimated cost, provider request hash, and whether a
   complete structured candidate existed.
2. Diagnose the 8/16 incomplete Flash completions without adding retry or
   fallback.
3. Extend scene/material-continuity and authoritative floor-owner verification
   so telephone drift and Hana-only formal-arrival selection cannot pass.
4. Revisit the Reasoner Auto-scope instruction/contract. The current model
   ladder confirms that all model families are being pulled toward the same
   conservative stop, which is primarily a prompt/contract issue.
5. After those corrections, compare Terra-low, Sol-low, and Sol-medium on at
   least 5-10 varied Reasoner-only cases. Include direct, indirect-memory,
   multi-character, emotional, and consequence-bearing cues. Qualify the
   Composer separately afterward.

Terra-medium may re-enter the shortlist only after floor-owner and scene-mode
continuity are enforced. Two samples of one doorway cue cannot establish model
promotion, universal logic quality, or a cache/latency promise.

## Evidence and call ledger

- Terminal evidence: `evaluation/evidence/codex_reasoner_model_ladder_2026-07-30_v3/summary.json`
- Per-sample evidence: `evaluation/evidence/codex_reasoner_model_ladder_2026-07-30_v3/samples/`
- Interrupted v1 and v2 evidence remains immutable and is labeled with the
  exact harness/cache-design interruption reason.
- The complete ladder consumed 28 Codex calls and 17 DeepSeek calls across v1,
  v2, and v3.
- The governed cumulative ledger is now 175/500 Codex and 77/500 DeepSeek,
  leaving 325 Codex and 423 DeepSeek calls.
- Story-authority writes: zero.
- Retries: zero.
- Fallbacks: zero.

