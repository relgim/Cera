# CERA

**Status:** Governed ordinary/relationship human testing is ready: 497/497 provider-free tests, live v27 at 10/10, and a real SillyTavern provisional-review/Accept smoke passed; the normal non-production world is connected on port 5101  
**Active repository:** `D:\AIChatBot\Cera`  
**Authority date:** 2026-07-28

CERA is a branch-safe character-reasoning and story-realization system for SillyTavern. Its core runtime shape is:

```text
deterministic Python turn kernel
-> bounded evidence retrieval
-> provider-neutral SceneReasonerPort (Codex first)
-> Python decision validation
-> DeepSeek Scene Composer
-> Python structural validation and provisional display
-> independent SceneRealizationVerifierPort / creator review
-> creator action
-> zero-provider atomic branch commit on Accept
```

For informed, freely consensual adult scenes, a future conditional `AdultMechanicsPort` may enrich protected mechanics without changing the Scene Reasoner's psychology or decisions. Exact protected source remains restricted to the Scene Composer. The mechanics route is never used for blocked non-consensual events.

This repository is the only future implementation target. Vera/V6 and all E-drive material are historical references and provenance, never runtime dependencies or automatic authority.

## Start here

Read [docs/START_HERE.md](docs/START_HERE.md). It gives the mandatory reading order, authority precedence, current phase gate, and document map.

## Current boundary

D-165 completes the behavioral-consolidation gate for the ordinary and
relationship route. Fresh live v27 passed ten consecutive turns with one
Sol-medium Reasoner, one DeepSeek V4 Flash thinking Composer, and one
Sol-medium verifier call per turn: 30 total calls, no retry or fallback. The
complete provider-free suite passed 497/497.

A real SillyTavern smoke displayed the provisional prose, Codex sequence plan,
Sol severity/reason, and creator actions. Accept committed one reviewed artifact
atomically with zero provider calls or retries. The normal non-production
human-test database and loopback port-5101 adapter are restored. This is ready
for governed creator testing, not production or universal prose-quality
qualification. Live Adult ON/EX publication, route promotion, production
binding, public deployment, and external-handler integration remain closed.
See the [D-165 result](docs/implementation/BEHAVIORAL_CONSOLIDATION_AND_HUMAN_TEST_GATE_RESULT.md)
and [current handoff](docs/handoff/CURRENT.md).

The repository contains controlling design documentation, the verified Phase 1 foundation, the verified Phase 2 SQLite authority store, accepted Genesis infrastructure and Hanezawa V1.2 child revision, the snapshot evidence service/Turn Kernel, provider-neutral Reasoner/Composer/verifier contracts, provider-free Adult ON/EX provenance and compiled craft catalog, blocker/resumption infrastructure, derived consolidation, offline evaluation, bounded live transports, request-scoped Codex evidence tools, transactional publication, one qualified local ordinary route, and a chat-ready local development adapter. No production story database or promoted route exists. Genesis and Adult EX artifacts are repository-local authority/provenance but have not been bound to a production world. This work did not:

- prove universal provider semantic quality or promote a production route;
- activate Adult EX or publish adult story content;
- create or seed a production story/runtime database;
- deploy a service;
- attach or inspect a user-owned external handler.

The creator authorized sequential work through provider-free Phase 9 and the provider-free Phase 10 evaluation foundation, subject to each explicit exclusion. Phase 9 received Pro's `PHASE_9_ACCEPTED_PROVIDER_FREE` advisory verdict, and the offline Phase 10 foundation received `PHASE_10_OFFLINE_EVALUATION_ACCEPTED`. See the current handoff.

Phase 11 passes 10/10 focused tests and the complete 197/197 suite with clean compilation, and received `PHASE_11_LIVE_PROVIDER_TRANSPORT_ACCEPTED`. Non-story live probes establish authentication, configured-route availability, isolation, one-call/no-retry behavior, and sanitized receipts only. They do not prove the concrete served Codex model, Codex judgment, DeepSeek prose, adult mechanics, or production readiness. See [the Phase 11 transport result](docs/implementation/PHASE_11_LIVE_PROVIDER_TRANSPORT_RESULT.md).

Phase 12 adds a required authenticated loopback MCP projection of the six existing typed evidence operations. Its local protocol and full 206-test suite pass, a corrected synthetic non-story live Sol probe made exactly one bound snapshot call with no failure, retry, fallback, authority write, or protected-content retention, and ChatGPT Pro returned `PHASE_12_CODEX_MCP_EVIDENCE_BRIDGE_ACCEPTED`. This proves the bridge boundary, not Scene Reasoner quality or story readiness. See [the Phase 12 bridge result](docs/implementation/PHASE_12_CODEX_MCP_EVIDENCE_BRIDGE_RESULT.md).

Phase 13 implements the typed Codex Reasoner packet, closed result schema, bridge lifecycle, strict decoding, supporting receipts, and the shared Python validation path. Nine focused tests and the complete 216-test suite pass; no Phase 13 provider call occurred; ChatGPT Pro returned `PHASE_13_CODEX_SCENE_REASONER_ADAPTER_ACCEPTED`. This is executable adapter infrastructure, not live schema/quality qualification or route activation. See [the Phase 13 adapter result](docs/implementation/PHASE_13_CODEX_SCENE_REASONER_ADAPTER_RESULT.md).

Phase 14 implements the typed DeepSeek Composer packet/result adapter, evidence-bound character/voice and craft context, quote-anchor conversion, provider-neutral receipts, and the existing Python acceptance path. Nine focused tests and the complete 225-test suite pass; no Phase 14 provider call occurred; ChatGPT Pro returned `PHASE_14_DEEPSEEK_SCENE_COMPOSER_ADAPTER_ACCEPTED`. This is live-shaped adapter infrastructure, not prose-quality evidence or route activation. See [the Phase 14 adapter result](docs/implementation/PHASE_14_DEEPSEEK_SCENE_COMPOSER_ADAPTER_RESULT.md).

Phase 15 connects the typed adapters through Python-owned context assembly using real Hanezawa evidence and fake provider transports. It reuses transient exact Reasoner evidence, adds only selected-character speech cards, preserves the adult safe/exact split, and emits a hash-bound uncommitted turn receipt. Six focused tests and the complete 231-test suite pass; no provider call or story write occurred; ChatGPT Pro returned `PHASE_15_LIVE_SHAPED_TURN_PIPELINE_ACCEPTED`. See [the Phase 15 pipeline result](docs/implementation/PHASE_15_LIVE_SHAPED_TURN_PIPELINE_RESULT.md).

Phase 16 adds Python-owned ordinary append/regeneration publication, SQLite schema version 8, immutable full receipt-payload evidence, topology binding before composition, exact replay, stale-head rejection, sibling regeneration, fork isolation, atomic rollback, and restart recovery. Seven new integration cases and the complete 238-test suite pass; no actual provider call or production data was used; ChatGPT Pro returned `PHASE_16_TRANSACTIONAL_ORDINARY_TURN_ACCEPTED`. See [the Phase 16 transaction result](docs/implementation/PHASE_16_TRANSACTIONAL_ORDINARY_TURN_RESULT.md).

Phase 17 corrects the ordinary-to-memory handoff by atomically committing one system-private direct accepted-turn event, then independently running pure rendering and event-bound deferred consolidation. SQLite schema version 9 preserves consolidation receipt payloads, evidence FTS rows update transactionally, and replay does not call the consolidator twice. Three new end-to-end cases raise the complete suite to 241; no actual provider call or production data was used. ChatGPT Pro returned `PHASE_17_POST_PUBLICATION_ACCEPTED`. See [the Phase 17 result](docs/implementation/PHASE_17_POST_PUBLICATION_RESULT.md).

Phase 18 schedules render, consolidation, and dependent derived-view work inside the accepted-story transaction. SQLite schema version 10 preserves pending/running/terminal work plus attempt history; no-change is durable, interrupted work fails closed, and failed work requires exact-ID plus reason authorization before retry. The expanded suite contains 245 tests with one optional live probe skipped. ChatGPT Pro returned `PHASE_18_DURABLE_POST_PUBLICATION_ACCEPTED`. See [the Phase 18 result](docs/implementation/PHASE_18_DURABLE_POST_PUBLICATION_RESULT.md).

Phase 19 adds a secret-safe operator service for actionable listing, pending dispatch, explicit interrupted recovery, and exact failed-work retry. Typed reports/errors/receipts expose IDs, states, counts, and hashes but no prose, evidence, protected identity, prompt, request payload, provider response, or raw operator rationale. Three new cases raise the complete suite to 248. ChatGPT Pro returned `PHASE_19_OPERATOR_SERVICE_ACCEPTED`. See [the Phase 19 result](docs/implementation/PHASE_19_OPERATOR_SERVICE_RESULT.md).

Phase 20 adds the final broad provider-free ordinary application facade. It joins typed prepared input, fake-transport Reasoner/Composer execution, atomic story/event/work publication, downstream dispatch, and the safe operator report. Exact replay is checked before any adapter and makes zero calls even when adapters are unavailable. Seven new cases raise the complete suite to 255. ChatGPT Pro returned `PHASE_20_PROVIDER_FREE_APPLICATION_ACCEPTED` and confirmed that no further provider-free ordinary milestone is required. See [the Phase 20 result](docs/implementation/PHASE_20_PROVIDER_FREE_APPLICATION_RESULT.md).

The requested final 20-run exercise then committed 20 sequential real-Genesis/fake-transport application turns covering all seven characters, multi-character turns, indirect memory, a preserved fork, regeneration, and zero-call replay after every run. It produced 20 artifacts, 20 direct events, 60 terminal work items, zero failures, clean SQLite integrity, and a complete 256-test pass. See [the final 20-run result](docs/implementation/FINAL_20_RUN_ACCEPTANCE_RESULT.md).

ChatGPT Pro returned `FINAL_20_RUN_ACCEPTANCE_ACCEPTED`, confirming that Phases 1-20 and the requested provider-free 20-run test complete the active authorized goal. This acceptance does not open any live, adult, product, or production gate.
