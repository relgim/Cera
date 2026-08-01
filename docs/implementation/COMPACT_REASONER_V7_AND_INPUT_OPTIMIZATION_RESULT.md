# Compact Reasoner V7 and Input Optimization Result

**Status:** Progressions 1-3 implemented and provider-free verified; Job 4 is a separate bounded live comparison
**Decision:** D-185
**Active runtime effect:** none; D-180 v6 remains active

## Recoverable baseline

The pre-tranche repository was first made clean by preserving the already
coherent D-183/D-184 correction as commit
`a25fd363819dd326014963f22fbc435543907106`. The local annotated tag
`cera-pre-compact-reasoner-v7-20260801T021530Z` points to that commit and is not
pushed. The commit SHA, not the tag name, is authoritative.

Frozen exact-fixture v6 identities at that baseline:

| Item | SHA-256 |
|---|---|
| Complete broad v6 prompt | `7a9b49b927206a18fa0995cc7df02d3b21895ae2cadb9e8110333ac8bc4a7811` |
| Broad v6 packet | `0bd29c436de37ebb8f3ab8cda2a2a629538a890bc7d7dca6ef9b18b8e83d594d` |
| Broad v6 provider schema | `4fc01c942f1ec4b8d3b6ae4e9c9220042fdf96c8b78f32ce4d20ed7ffbb19620` |
| v6 stable instructions | `3075e396a8d518e35baf5c01f75f0fd90f11666d5eb656939b76c2c1e005b327` |
| Baseline `reasoner/codex.py` bytes | `2e3a64c22a7241d9546b4d5fcd6b580fa99fe01b1a22f16333012a2a0e8f7c0a` |
| Baseline `reasoner/drafts.py` bytes | `2c7194e3ca406978ef7369d65d2aa105477dce9807967c658944f68308503aac` |
| Baseline `active_runtime.py` bytes | `289a36d3b8415071b3a7de70ce5b636ab7d7a97235d30b8ed96c73cd15061e08` |

The active identities remain Reasoner adapter v25, packet v14, prompt v25,
MCP v7, draft v6, and native stored branch sessions. DeepSeek Composer and the
independent Sol verifier are unchanged.

## Progression 1: truthful cumulative operation telemetry

The native stored-turn worker now observes the complete streamed Codex turn.
It de-duplicates repeated thread-total notifications and sums each unique
operation-local `usage.last` step. The four-step historical continuation sample
therefore reports 281,179 input, 204,032 cached input, 77,147 uncached input,
5,304 output, and 2,171 reasoning tokens instead of exposing only its last
step.

`cera.codex_operation_telemetry.v1` records hashed request, provider operation,
candidate thread, provider root, accepted-parent checkpoint, and candidate
checkpoint identities; packet/request/evidence/reasoning/structured-output/
completion/parse/validation timestamps; every evidence-tool start/completion;
per-step and cumulative usage; one actual provider attempt; finish status; and
an explicit transport error when applicable. Unsupported data is null and
named. Prompt, output, reasoning, tool arguments, private evidence, and secrets
are never retained.

The existing provider receipt remains v2. Its counters now truthfully reflect
the complete stored Reasoner operation. The existing configured output budget
still applies to the final structured-output step, so cumulative intermediate
reasoning is not misclassified as one oversized final response.

## Progression 2: authoritative scene scope and compact input

Python now represents world-known, physically present, scene-reachable,
currently active, exact-source, eligible, and selected cast separately. For the
frozen request, “Continue the scene with only the current characters as they
talk to eachother,” the exact accepted generation-2 head identifies Hana and
Mia as the active NPCs. Ted remains present and protected; Sakura, Enne, Tomi,
Aoi, and Yuuni remain world-known and scene-reachable but are not sent as
eligible responders. The rule reads accepted state and does not hardcode Hana
or Mia.

The scoped preparer reduces exact seed records from 18 to 8. Compact evidence
removes repetitive provenance serialization while retaining exact supported
section content and all authority, truth, ownership, knowledge, visibility,
version, hash, and relevance boundaries. A local reading capsule carries only
alias-based reconstruction aids. It is not memory or story truth and is rebuilt
after every lifecycle boundary.

## Progression 3: compact Reasoner v7 shadow contract

`CodexReasonerDraftV7Compact` removes provider-authored structural duplication.
Runtime Codex still owns responder selection, floor, perception, intent,
tactic, knowledge limits, source classification, every ordered causal beat,
material transitions, stop boundary, future conditionals, adult craft when
already activated, and evidence-backed development proposals. Python expands
those semantics mechanically into the existing v6 draft, then runs the
unchanged authoritative compiler and validators. The Composer still receives
the same domain plan type and retains full prose ownership.

Both compact packet and v7 draft selection require explicit constructor flags.
All defaults remain v6. The active runtime constructs no compact selector, and
the D-180 profile contains no v7 identity.

## Provider-free anatomy

The analysis script opens the source SQLite database read-only, copies it into
a disposable directory, and performs no provider call or story write.

| Variant | Seed records | Prompt bytes | Estimated prompt tokens | Schema bytes | Estimated schema tokens |
|---|---:|---:|---:|---:|---:|
| Broad input + v6 | 18 | 50,389 | 12,597 | 20,090 | 5,022 |
| Scoped compact input + v6 | 8 | 36,375 | 9,094 | 19,290 | 4,822 |
| Scoped compact input + v7 | 8 | 21,659 | 5,415 | 11,032 | 2,758 |

Token counts above are four-bytes-per-token anatomy estimates, not provider
usage. An illustrative two-beat equivalent output is 3,302 bytes in v7 and
4,488 bytes after deterministic v6 expansion; it is not a model result.

## Verification and remaining gate

- Focused telemetry, compact-v7, creator-review, provider, Reasoner, and stored-
  session checks: 94/94 passed in 39.287 seconds.
- Complete provider-free suite: 648/648 passed in 310.714 seconds; one optional
  live test skipped.
- Python compilation, `git diff --check`, exact source-database integrity, and
  active v6 prompt/packet/schema hash comparison: passed.
- Provider calls: zero for Progressions 1-3.
- DeepSeek calls, verifier calls, retries, fallbacks, story writes, service
  restarts, SillyTavern changes, deployments, and route activation: zero.

Job 4 is the only authorized live gate. It compares broad-v6, scoped-v6,
scoped-v7, and reconstructed-context scoped-v7 using exactly one Sol-medium
attempt each in an isolated worktree and disposable database. It cannot call
DeepSeek or the verifier, publish story state, activate v7, or authorize Job 5.
