# Stored-thread performance reassessment

Status: complete, provider-free.

The compact Call 2 submission was genuinely lean, but the stored-thread experiment did not demonstrate lower model input or lower latency. Call 2 sent only 5,512 variable bytes versus 31,112 for Call 1, yet reported 28,265 input tokens versus 17,454 and took 94,222.081 ms versus 90,777.217 ms.

## Input accounting

| Component | Call 1 | Call 2 | Exact provider token share |
|---|---:|---:|---|
| Variable turn submission | 31,112 bytes | 5,512 bytes | Not separately reported |
| Stored-thread base instructions | 4,903 bytes at thread creation | 0 newly submitted bytes | Later context contribution unavailable |
| Structured output schema | 3,080 bytes | 3,080 bytes | Passed each turn; share unavailable |
| MCP declarations and app-server framing | Unavailable | Unavailable | Unavailable |
| Accepted-context injection | 0 bytes | 438 bytes | Stored-history share unavailable |
| Prior stored conversation | None | Present | Provider serialization unavailable |
| Observed model input | 17,454 tokens | 28,265 tokens | Authoritative operation-local report |

The SDK distinguishes `usage.last` from `usage.total`. CERA records each distinct `last` value as an operation-local step and preserves the provider's thread-cumulative totals. Call 2's thread-total input was 45,719 tokens, exactly 17,454 plus 28,265. Therefore 28,265 is the second operation's input, not a mistaken thread-total copy.

## What is known

- Both calls used the same physical thread.
- The Call 2 prompt omitted the base instructions, complete Call 1 prompt/output, unchanged character summaries, Composer material, and Validator material.
- Call 2 reached provider completion and then failed local validation because it copied a prior accepted-context ID into the current output field.
- Both calls reported zero cached input tokens at both operation-local and thread-total levels.

## What is inferred

The small Call 2 submission was only one part of the model input. Stored conversation, base/schema/tool framing, the 438-byte accepted-context injection, or other provider-owned context also contributed. The exact allocation is unavailable, so no individual hidden component is assigned a fabricated token count.

Stored-thread continuation showed no measured latency improvement in this observation. That statement is descriptive, not causal.

## What is unavailable

- Exact tokens attributable to system/base instructions, output schema, MCP declarations, prior conversation items, and provider framing.
- Whether prior reasoning items entered the next model context.
- Cache eligibility, cache-key composition, cache miss reason, or an unreported provider cache layer.
- Queue, model-compute, and structured-decoding latency splits.

The two zero-cache counters are authoritative reports for these operations, but they do not establish why caching was absent. This reassessment therefore makes no claim that caching caused either timing result.

## Minimum materially different experiment

One fresh-thread compact-context Sol-medium control is the smallest useful next test. It preserves the compact validated continuation packet, schema, model, effort, and validation while removing accumulated provider conversation. Repeating the stored-thread call would not isolate the suspected context contribution.

The machine-readable evidence and exact hashes are in `STORED_THREAD_PERFORMANCE_REASSESSMENT.json`.
