# DeepSeek Completion and Continued-Session Failure Correction Result

**Date:** 2026-07-30  
**Decision:** D-169  
**Status:** implemented, provider-free verified, and minimally live-confirmed  
**Promotion:** none

## Outcome

The two observed shared failure classes are corrected without accepting invalid
output, retrying, or falling back:

1. A completed DeepSeek response that is unusable now preserves its exact
   normalized finish reason plus privacy-safe timing, model, usage, cost, and
   hash evidence. Partial prose and provider reasoning are never retained.
2. A test-only continued Codex thread is invalidated after any failed pipeline
   stage. A later explicit turn starts a fresh provider thread; accepted CERA
   evidence remains the only continuity authority.

The default Flash Composer call now explicitly disables thinking. Codex remains
the causal planner and DeepSeek remains the prose realizer. This avoids spending
the shared completion budget on a second hidden planning process. Thinking can
still be enabled explicitly for a separately identified probe, and its adapter
evidence hash differs from non-thinking mode.

Reasoner adapter/prompt v24 adds a mandatory non-ready reset: an
`insufficient_evidence` or `blocked` result must clear every tentative decision
field before output. Python still rejects any surviving forbidden field; no
invalid status is normalized or accepted.

## Version and contract changes

- Codex Reasoner adapter/prompt: v23 -> v24.
- DeepSeek Composer adapter: v28 -> v29; packet v15 and prompt v26 unchanged.
- DeepSeek route adapter identity: v24 -> v25.
- New registered safe evidence schema:
  `cera.provider_failure_call_receipt.v1`.
- `LiveProviderCallReceipt` v2 remains the nested call record and is not
  reinterpreted.
- Active domain schemas and publication validators are unchanged.

The failure wrapper records one controlled finish reason (`stop`, `length`,
`content_filter`, `tool_calls`, or `insufficient_system_resource`), one failure
kind, and `completion_accepted=false`. Durable diagnostics use distinct
uppercase value-free tokens while the typed receipt retains the exact enum.

## Provider-free evidence

Focused contract/pipeline suite:

```text
84/84 passed in 63.718 seconds
```

Complete repository suite:

```text
519/519 passed in 477.909 seconds
```

`py_compile` also passed for every changed Python module and harness.

Coverage includes:

- every documented non-stop DeepSeek terminal finish reason plus
  response-shape failures;
- exact usage/finish preservation through transport, Composer, runtime audit,
  registry decode, and privacy-safe failure payload;
- exclusion of partial output, prompt, source, evidence, and credentials;
- no retry, fallback, publication, or state mutation;
- distinct adapter evidence for thinking enabled versus disabled;
- Sol non-ready reset prompt semantics and unchanged Python rejection;
- continued-thread invalidation after a failed pipeline stage.

## Minimal live confirmation

Evidence is immutable under:

`evaluation/evidence/deepseek_completion_correction_probe_2026-07-30_v1/`

One disposable Long doorway turn used one fresh Sol-medium thread, one
DeepSeek V4 Flash non-thinking Composer call, and the scripted verifier. It
passed on the first attempt:

| Measure | Result |
|---|---:|
| Sol calls | 1 |
| DeepSeek calls | 1 |
| retry/fallback | 0 / 0 |
| story commits/artifacts | 0 / 0 |
| total wall time | 120.383 s |
| Sol provider duration | 103.588 s |
| DeepSeek provider duration | 7.905 s |
| Sol input/output/reasoning tokens | 22,174 / 4,881 / 2,118 |
| DeepSeek input/output/reasoning tokens | 11,427 / 735 / 0 |
| accepted in-memory candidate | 314 words / 1,863 characters |
| disposable SQLite integrity | `ok`, 0 foreign-key findings |

This confirms that the corrected non-thinking path can return one complete
candidate and that DeepSeek is not the dominant latency in this sample. One
success does not establish a universal completion rate or prose-quality
promotion.

## Runtime state and remaining work

The loopback development adapter was restarted after verification and is
healthy at `127.0.0.1:5101`; `/v1/models` returns `cera-alpha`. The existing
human-test database was not reset or modified by the disposable probe.

D-169 closes completion observability and the observed continued-thread
contamination path. It does not close the separate D-168 floor-owner,
doorway/telephone continuity, or conservative Auto-scope quality findings.
Those require shared validator/prompt work and a later diverse comparison, not
case-specific exceptions. Live Adult ON/EX publication, production binding,
route promotion, deployment, and external-handler work remain closed.

The conservative governed call ledger is now 176/500 Codex and 78/500
DeepSeek, leaving 324 and 422 respectively.
