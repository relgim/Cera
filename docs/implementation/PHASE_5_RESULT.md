# Phase 5 Result — Provider-Neutral Scene Reasoner and Scripted Fake

**Status:** accepted; Pro returned `CONTINUE_TO_PHASE_6_FAKE_COMPOSER`  
**Date:** 2026-07-28  
**Scope:** provider-neutral contracts, scripted fake adapter, validation, receipts, and deterministic tests

```text
live_codex_transport_implemented = false
live_provider_calls_made = false
fake_adapter_production_enabled = false
real_creator_genesis_available = false
canonical_character_records_installed = false
story_publication_enabled = false
```

## Outcome

Phase 5 implements the reasoner boundary without turning Python into a character-psychology engine and without adding a runtime provider.

Implemented:

- provider-neutral `SceneReasonerPort`;
- test/development-only `FakeSceneReasonerPort` whose behavior is a declarative fixture and tool-call script;
- production rejection of the fake adapter;
- `SceneReasonerRequest` binding the prepared turn, exact source hash, matching source-unit identities, one immutable snapshot, seed dossier, eligible/aware cast, and hard boundaries;
- ordinary exact source mode and adult non-graphic causal-ledger mode, with exact protected adult prose prohibited from the latter;
- tool mappings for `get_turn_snapshot`, `resolve_entities`, `search_evidence`, `fetch_evidence`, `get_character_sections`, and `get_continuity`;
- fresh per-invocation evidence accounting with the same immutable request snapshot and no provider conversation state;
- explicit `decision_ready`, `insufficient_evidence`, and `blocked` outcomes;
- operational-only structured output: `SceneDecision`, participation/floor selections, versioned evidence citations, uncertainties/blocker state, and advisory state deltas;
- deterministic validation for Python route ownership, eligible and aware participants, one lead/floor owner, justified secondary participation, protected-user exclusion, operational beat actors, and no direct story artifact;
- hard-reference validation requiring exact authorized evidence, matching record ID/version, active snapshot scope, and owner-private use only by its owner;
- state-delta validation through the Phase 4 Turn Kernel without persistence;
- provider-neutral reasoner receipts binding request hash, snapshot, source hash, fixture identity/hash, outcome hash, evidence-operation receipts, tool calls, returned bytes, status, and zero external calls;
- a domain-separated hash of the exact safe reasoner source view, allowing later Composer packets to prove which non-graphic ledger the reasoner actually received without exposing its text;
- deterministic replay for the same request, snapshot, fixture, and evidence state;
- explicit no-fallback failures for unavailable adapters, evidence service/index failures, stale snapshots, invalid outputs, and exhausted budgets.

The fake contains no character-name branches, phrase routing, personality arithmetic, scenario outcomes, or production reasoning rules. It proves orchestration and validation only.

## Evidence-tool mapping

- `get_turn_snapshot` revalidates and returns the already prepared immutable snapshot; it creates no second authority.
- `resolve_entities` maps to bounded server-filtered evidence search by typed entity IDs.
- the remaining four operations delegate directly to the Phase 4 evidence service under the same snapshot.

Search references may guide a later exact fetch. They cannot support a hard decision citation by themselves.

## Verification

Repository validation passed:

- Python bytecode compilation: pass;
- deterministic unit tests: **76/76 pass**;
- documentation validation: pass with zero findings.

Phase 5 adds 14 focused tests covering:

- ordinary direct reasoning with snapshot/entity/search/exact-fetch tools;
- deterministic outcome and receipt replay;
- multi-character floor and justified secondary intervention;
- unaware/ineligible and protected-user selection rejection;
- false-memory or missing-evidence uncertainty without invented history;
- search-only evidence rejection for hard decisions;
- cross-owner private-evidence rejection;
- unknown/stale citation rejection;
- stale snapshot between tool calls;
- evidence index outage and retrieval-budget exhaustion with no fallback;
- advisory state-delta validation with no commit;
- adult preflight preventing invocation and safe non-graphic adult ledger enforcement;
- production fake rejection and stable unavailability;
- blocked operational outcome and strict rejection of a finished-prose field;
- fixture/receipt identity mismatch rejection before invocation.

The complete 76-test suite also retains Phase 4 cross-world, cross-branch, supersession, linked-privacy, byte/depth/count, restart, and no-write guarantees exercised through the same delegated evidence service.

## Deliberately not done

- no `CodexSceneReasonerPort` transport or provider call;
- no Codex CLI, app-server, SDK, MCP runtime, Responses API, or account/session automation;
- no provider credential/configuration mutation;
- no real Genesis, character-specific content, or production database;
- no DeepSeek Composer or Adult Planner;
- no Adult EX, SillyTavern, external handler, deployment, story publication, or durable state integration;
- no claim that fake tests establish runtime reasoning quality, realism, latency, or provider reliability.

## Review result

Pro returned `CONTINUE_TO_PHASE_6_FAKE_COMPOSER` and required a distinct Composer request, declarative fake, complete-Core candidate, structural realization manifest, protected-user and semantic-separation checks, creator-event coverage, presentation-neutral in-memory acceptance, pure rendering, domain-separated receipts, and no provider/publication/state writes.
