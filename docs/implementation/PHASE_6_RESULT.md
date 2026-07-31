# Phase 6 Result — Provider-Neutral Scene Composer and Structural Acceptance

**Status:** accepted; Pro returned `CONTINUE_TO_PHASE_7_FAKE_ADULT_ROUTE`  
**Date:** 2026-07-28  
**Scope:** fake-only Composer contracts, structural validation, in-memory acceptance, rendering separation, receipts, and deterministic tests

```text
live_deepseek_transport_implemented = false
live_provider_calls_made = false
fake_adapter_production_enabled = false
real_creator_genesis_available = false
adult_ex_material_imported = false
story_publication_enabled = false
authority_store_writes_from_composer = false
```

## Outcome

Phase 6 implements the complete Composer contract path without connecting DeepSeek or publishing story state.

Implemented:

- provider-neutral `SceneComposerPort` and test/development-only `FakeSceneComposerPort`;
- a declarative fake fixture that returns one configured candidate and manifest, with no character, phrase, genre, adult-family, psychology, or scenario rules;
- production rejection and explicit unavailable failure with no retry or fallback;
- a distinct `SceneComposerRequest` binding the prepared turn, immutable snapshot through the reasoner receipt, exact source and source-unit hashes, decision hash, SequencePlan hash, selected cast, floor, scene scope, response profile, continuity references, hard boundaries, and creator-event coverage mode;
- separate ordinary exact-source units and a restricted protected-source envelope;
- separate bindings for the reasoner's safe source view and the restricted exact protected-source envelope, neither of whose text appears in Composer or validation receipts;
- strict source-unit text/hash/classification/order checks before dispatch;
- a raw `ComposerCandidate`, separate `RealizationManifest`, fake provider receipt, Python validation receipt, in-memory `AcceptedStoryArtifact`, and downstream `RenderedStory`;
- complete-Core enforcement: outlines, partial drafts, Detailer-dependent results, terminal fragments, boundary crossing, and major unauthorized additions reject;
- structural decision fidelity for cast, floor, selected intent/action hashes, exact current-beat order, terminal-state preservation, and protected-user stop boundary;
- creator-event coverage from the earliest supplied beat through every source unit in order, including explicit attempted/ongoing/partial/interrupted/completed state preservation;
- protected-user realization spans limited to an exact source unit, allowed realization kind, and identical source progression state; private state, motive, consent/refusal, reaction, departure, destination, or commitment cannot be authored;
- structural rejection of declared body/vocalization/freeze/silence/compliance-to-consent or desire inferences and belief/allegation/suspicion-to-objective-fact promotion;
- presentation-neutral acceptance that rejects UI HTML, prompt/plan/manifest labels, provider diagnostics, and bookkeeping;
- advisory realization metadata retained only as non-durable data when well formed; malformed advisory metadata is quarantined without weakening hard candidate validation;
- a pure presentation renderer that accepts only `AcceptedStoryArtifact`, creates a separately hashed projection, performs no semantic repair, and preserves the accepted-artifact hash across profiles;
- domain-separated deterministic identities and hashes for request, candidate, manifest, provider receipt, validation receipt, accepted artifact, reserved future transaction identity, and rendered output;
- no database adapter dependency in the Composer coordinator and no generation advance, event/memory/state creation, commit, publication, network, provider, Detailer, repair, or fallback call.

The `AcceptedStoryArtifact.transaction_id` produced in Phase 6 is a deterministic reservation for a future atomic commit bundle. It is not evidence of persistence. Only a later `CommitReceipt` can establish that the artifact became durable branch truth.

## What deterministic validation does and does not prove

Phase 6 performs **structural candidate validation**. It proves that declared IDs, hashes, spans, cast, beat/source coverage, route bindings, protected-user permissions, semantic separation declarations, and presentation boundaries agree.

It does not prove that the prose is psychologically convincing, stylistically strong, semantically faithful where a provider misreports its manifest, or acceptable to a human reader. Those claims require a qualified live provider, adversarial provider-output evaluation, and creator/human review. The validation receipt explicitly records `semantic_quality_proven = false`.

## Verification

Repository validation passed before this report:

- Python bytecode compilation: pass;
- deterministic unit tests: **90/90 pass**;
- documentation validation: pass with zero findings.

Phase 6 adds 14 focused tests covering:

- valid ordinary in-memory acceptance, deterministic replay, zero provider calls, and zero authority-store writes;
- multi-character exact-cast enforcement;
- intent, action-direction, floor, and beat drift rejection;
- complete ordered creator-event coverage, earliest-beat start, missing/reordered unit rejection, and event-state preservation;
- exact-source protected-user action allowance and unauthorized dialogue/private-state/progression/character rejection;
- belief/suspicion promotion and physiological-response-to-consent rejection;
- separate adult safe-ledger and protected-envelope hash binding without exact-text receipt leakage;
- internal label, UI markup, and diagnostic leakage rejection;
- complete-Core, no-Detailer, terminal-state, stop-boundary, and major-addition enforcement;
- strict unknown-field rejection;
- advisory metadata quarantine without promotion or commit;
- renderer input/type separation and multiple-profile artifact-hash preservation;
- production fake rejection, stable unavailability, and no retry;
- exact source tamper and fixture/receipt identity rejection before invocation.

## Deliberately not done

- no `DeepSeekSceneComposerPort`, provider credential, endpoint, model selection, or call;
- no live `CodexSceneReasonerPort`;
- no real Genesis, character-specific content, Adult EX example, or production database;
- no conditional Adult Planner implementation (Phase 7);
- no semantic verifier, provider repair/revision, automatic Detailer, state/event integration, commit, publication, SillyTavern renderer, external handler, or deployment;
- no claim that fake tests establish prose quality, realism, provider reliability, latency, or production readiness.

## Review result

Pro returned `CONTINUE_TO_PHASE_7_FAKE_ADULT_ROUTE`. Its decisive clarification was that Phase 7 must not introduce a second psychological authority: the Scene Reasoner still owns character intent/tactic/consequences, while any conditional adult component is limited to typed authority, synchronized context, and non-authoritative mechanics enrichment.
