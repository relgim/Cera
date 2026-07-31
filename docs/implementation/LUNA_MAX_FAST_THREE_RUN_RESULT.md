# Luna 5.6 Max + Fast Three-Run Result

**Date:** 2026-07-30  
**Decision:** D-170  
**Status:** terminal 0/3 at Reasoner stage  
**Route promotion:** none

## Requested configuration

The local Codex app-server capability response advertised:

- model `gpt-5.6-luna` (`GPT-5.6-Luna`);
- reasoning efforts `low`, `medium`, `high`, `xhigh`, and `max`;
- service tier `priority`, displayed as `Fast` and described as 1.5x speed
  with increased usage.

The test therefore used the actual highest advertised reasoning effort
(`max`) and explicitly requested the actual Fast tier (`priority`). The current
CERA provider receipt does not echo the served service tier, so evidence proves
that the app-server advertised and accepted the request, not independent
provider confirmation that every turn was served at that tier.

Each requested sample used the same real-V1.2 Long doorway request:

```text
Hello, my name is Ted. Is this the hanezawa household?
```

Each sample had a fresh isolated workspace, worker process, and ephemeral Luna
thread. CERA's supervised worker enforced the existing 240-second route
timeout and killed the complete worker tree on timeout. A successful Reasoner
would have continued to the current non-thinking DeepSeek Flash Composer and
Sol-medium verifier. No retry, fallback, publication, or story commit was
permitted.

## Terminal results

| Run | Wall time | Terminal result | Composer | Verifier |
|---|---:|---|---:|---:|
| 1 | 240.180 s | `CERA_REASONER_UNAVAILABLE`: timed out | 0 calls | 0 calls |
| 2 | 220.581 s | `CERA_PROVIDER_BUDGET_EXCEEDED`: output exceeded 16,384-token ceiling | 0 calls | 0 calls |
| 3 | 240.132 s | `CERA_REASONER_UNAVAILABLE`: timed out | 0 calls | 0 calls |

Aggregate:

- full CERA passes: 0/3;
- Luna calls: 3;
- DeepSeek calls: 0;
- Sol verifier calls: 0;
- retries/fallbacks: 0/0;
- story artifacts/commits: 0/0;
- SQLite integrity: `ok`, zero foreign-key findings;
- stray test worker processes after completion: 0;
- normal port-5101 CERA service: healthy.

No reasoning, sequence, or story-quality comparison is possible because no
sample produced a Reasoner outcome accepted by Python. Run 2 establishes that
Luna-max can consume the entire current Reasoner output allowance before
returning an acceptable bounded result. The exact rejected output-token count
is not retained by the current Codex failure path, which is an observability
limitation; the safe terminal classification remains reliable.

## Assessment

Luna-max/Fast is not suitable for CERA's current Reasoner route. Fast tier did
not offset the cost of maximum reasoning: two samples exhausted the time
ceiling, and the only sample that returned earlier exceeded the output ceiling.
This is materially worse than the previous Luna-xhigh evidence (181.4-223.4
seconds for structurally valid Reasoner results) and Sol-medium evidence
(95.2-110.4 seconds in the model ladder; 103.588 seconds in D-169's live
confirmation).

Do not increase the timeout or output ceiling merely to make Luna-max pass.
That would increase latency and quota consumption without evidence of better
story decisions. Keep Sol-medium active. If Luna is reconsidered later,
Luna-high or xhigh would be a more defensible bounded comparison than max, but
the earlier Luna results still produced 0/2 full-route passes.

## Harness evidence and call accounting

Terminal v3 evidence:

`evaluation/evidence/luna_max_fast_three_run_2026-07-30_v3/`

- summary SHA-256:
  `5ff85a712bc92c05380cf1e5ab3c1d2ac68a00e60a1a0dbd3583fd2d32441651`;
- Sample 1 SHA-256:
  `d0b1f8374557e93272103ce06659f6d7b18fc3d913fc5ac7e862426f863db8ee`;
- Sample 2 SHA-256:
  `9b7de09dee979ddec48465a255f948f083a5732a8423cb858966cf88dbc0041f`;
- Sample 3 SHA-256:
  `bfae51b1b2404e0b7b3d1b84fd8d1170488b1541f988ce845f4038a61e108210`.

Two harness diagnostics are also preserved:

- v1 stopped before dispatch because the app-server capability object was not
  converted to primitive JSON;
- v2 passed capability preflight but exposed an unbounded in-process runner.
  Codex interrupted it after one Luna dispatch exceeded 600 seconds and killed
  the exact process tree. It made no Composer/verifier call or story write.

The requested three-run v3 batch consumed three Luna calls. Including the one
conservatively counted v2 dispatch, this task consumed four Codex calls and
zero DeepSeek calls. The governed cumulative ledger is now 180/500 Codex and
78/500 DeepSeek, leaving 320 and 422 respectively.
