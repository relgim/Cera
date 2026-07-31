# CERA

<!-- CERA_CURRENT_RUNTIME_BEGIN -->
**Status:** D-180 is the current local-development runtime identity. Reasoner v25/packet v14 uses MCP v7 in the D-179 branch-bound stored session; DeepSeek V4 Flash Composer v29/packet v15/prompt v26 is non-thinking; the independent Sol verifier remains v8/request v7.
**Active repository:** `D:\AIChatBot\Cera`
**Status date:** 2026-07-31
**Active runtime profile:** `cera.active_runtime.d180.v1`
**Active runtime profile SHA-256:** `f74347604d9adb7be0ded2a3c9c62e7076aca4d5d773aa8a3c97f8e7bcee0801`
**Reasoner identity:** `cera.codex_scene_reasoner.v25`; `cera.codex_scene_reasoner_packet.v14`; `cera.codex_scene_reasoner_prompt.v25`; `cera.reasoner_evidence_mcp.v7`; `branch_bound_native_stored_v1`
**Composer identity:** `deepseek-v4-flash`; `cera.deepseek_scene_composer.v29`; `cera.deepseek_scene_composer_packet.v15`; `cera.deepseek_scene_composer_prompt.v26`; non-thinking
**Verifier identity:** `cera.codex_scene_realization_verifier.v8`; `cera.codex_scene_realization_verifier_prompt.v8`; `cera.scene_realization_verification_request.v7`; Sol-medium
**Provider-free checkpoint verification:** 575/575 tests passed; zero live provider calls
<!-- CERA_CURRENT_RUNTIME_END -->

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

D-180 corrects the active Reasoner evidence-citation contract without creating
a v26 identity: Reasoner adapter/prompt v25, packet v14, and MCP v7 are the
current source. Python still validates every alias against the current bounded
evidence allocation before any provisional candidate can reach creator review.
The current DeepSeek default is Flash with thinking disabled. The canonical
profile above is used by routes, stored-session compatibility, health output,
tests, and current-status validation.

The earlier D-165, D-177, and D-179 results remain historical evidence. D-179's
5/5 stored-session qualification is the last live activation evidence, not a
fresh end-to-end qualification of every D-180 component. This checkpoint makes
zero provider calls, passes 575/575 provider-free tests, and does not claim a
new live pass. Adult ON/EX publication,
route promotion, production binding, public deployment, and external-handler
integration remain closed. See the [current handoff](docs/handoff/CURRENT.md).

## Historical milestone record

The material below preserves evidence from earlier gates. Any historical use
of “current” is scoped to that checkpoint and does not override the D-180
profile above.

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
