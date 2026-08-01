# Compact Reasoner V7 Job 4 Result

**Task:** `compact-reasoner-v7-four-variant-sol-medium-comparison-v1`
**Status:** completed
**Authorized attempts initiated:** 4/4
**Actual provider calls observed:** 4
**Model/effort/Fast:** `gpt-5.6-sol` / `medium` / disabled

No DeepSeek, verifier, retry, fallback, story write, active-route change, service change, deployment, remote operation, or push occurred.

## Variant results

| Variant | Context | Contract | Status | Observed elapsed s | Input | Cached | Uncached | Output | Reasoning | Tools | Responders | Beats |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|
| A | stored_accepted_ancestry | cera.codex_reasoner_draft.v6 | validated | 113.69 | 65217 | 0 | 65217 | 4910 | 1757 | 0 | character:hana_hanezawa, character:mia_hanezawa | 3 |
| B | stored_accepted_ancestry | cera.codex_reasoner_draft.v6 | validated | 92.845 | 60706 | 0 | 60706 | 3917 | 1204 | 0 | character:hana_hanezawa, character:mia_hanezawa | 3 |
| C | stored_accepted_ancestry | cera.codex_reasoner_draft.v7.compact_shadow | failed | 91.503 | 56248 | 0 | 56248 | 4365 | 2390 | 0 | - | 0 |
| D | deterministic_reconstructed_context | cera.codex_reasoner_draft.v7.compact_shadow | failed | 133.333 | 16324 | 0 | 16324 | 4742 | 2588 | 0 | - | 0 |

## Findings

- B validated 18.4% faster than A by provider duration while retaining all recorded cast, causal-block, citation, adult-route, and protected-user checks.
- Scoped preparation reduced provider-reported input 6.9% and output 20.2% relative to broad v6 in this stored-history fixture.
- C and D are not valid quality or promotion candidates: both completed provider generation but failed the same typed compact-v7 contract.
- Compact v7 did not reduce output tokens here: C and D emitted more output than valid scoped-v6 B.
- Fresh reconstructed D used far fewer input tokens than stored-history B/C but was slower, so token count alone did not determine wall latency in this four-call sample.

## Compact-v7 validation diagnosis

- C and D both returned `decision_ready` with every required selector populated except `reason_code`, which was `null`.
- The compact-v7 prompt did not explicitly state the ready-state `reason_code` requirement even though the typed contract requires it. Python correctly rejected both outputs; no hidden repair or retry occurred.
- This is a shadow-contract defect to correct and requalify only under a separately authorized progression. It does not justify production activation.

## Interpretation boundary

The report records provider-reported cumulative usage and content-free stage telemetry. Prompt/schema byte anatomy is deterministic; token estimates from Progressions 1-3 were not provider measurements. The actual Job 4 schema anatomy includes all request-bound fetch aliases and therefore supersedes any smaller seed-alias-only schema-size estimate for live-call accounting.

A valid four-case comparison is diagnostic evidence, not route promotion. Compact v7 remains shadow-only and Job 5 is not authorized.

## Pro overlap

Final safe-boundary poll: `{"boundary":"job4_complete","present":false,"stop_requested":false}`
