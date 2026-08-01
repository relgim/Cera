# Progression 1 Result - Cumulative Codex Operation Telemetry

task_id: `compact-reasoner-v7-cumulative-telemetry-v1`
status: completed

The stored-turn worker now observes the complete SDK event stream, de-duplicates
repeated cumulative notifications, and sums every unique operation-local
`usage.last` step. The known four-step continuation sample totals 281,179
input, 204,032 cached input, 77,147 uncached input, 5,304 output, and 2,171
reasoning tokens rather than only the final step.

The versioned privacy-safe record includes hashed request/operation/thread/root/
checkpoint identities, supported stage timestamps, evidence-tool timings,
per-step and cumulative usage, actual attempt count, finish state, transport
error, and explicit unsupported fields. It retains no provider content.
Synthetic accumulation, duplicate, backward-total, identity, null/unknown,
phase-binding, and transport-error tests pass. The active v6 prompt/schema and
one-attempt/no-fallback behavior are unchanged.
