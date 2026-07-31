# Phase 7 Result — Synthetic Consent-Valid Adult Route

**Status:** accepted; Pro returned `PHASE_7_ACCEPTED_INFRASTRUCTURE_COMPLETE`  
**Date:** 2026-07-28  
**Scope:** Python adult authority, dual-view synchronization, current-turn craft context, fake mechanics enrichment, existing Reasoner/Composer integration, and deterministic tests

```text
live_adult_provider_implemented = false
live_provider_calls_made = false
real_creator_genesis_available = false
real_adult_authority_installed = false
adult_ex_material_imported = false
actual_character_content_used = false
story_publication_enabled = false
authority_store_writes_from_adult_route = false
```

## Outcome

Phase 7 implements a synthetic, offline consent-valid adult route around the existing Scene Reasoner and Scene Composer. It deliberately does **not** create a second psychological planner.

Implemented runtime shape:

```text
Python adult authority and immutable evidence revalidation
-> synchronized non-graphic causal ledger for SceneReasonerPort
-> existing Python reasoner validation
-> optional non-authoritative AdultMechanicsPort enrichment
-> restricted exact source plus validated adult binding for SceneComposerPort
-> existing Phase 6 structural acceptance and pure rendering boundary
```

Implemented contracts and behavior:

- `AdultAuthorityDecision` requiring one exact authority record per listed participant, confirmed adulthood, current presence/eligibility, granted consent, clear capacity, no pressure, freedom to stop, same-snapshot exact evidence, and synthetic/noncanonical Phase 7 authority;
- distinct typed concepts for provider capability, scene eligibility, identity/adulthood, consent, capacity, pressure, freedom, scenario classification, desire, attraction, pleasure, bodily response, preference, experience, intention, progression state, and relationship meaning;
- explicit scenario separation among consensual activity, consensual roleplay, uncertain/absent consent, withdrawn consent, and actual non-consensual conduct;
- explicit adult-route failure for absent/uncertain/withdrawn authority and blocker routing for actual non-consensual conduct;
- `AdultCausalLedger` containing source-unit IDs/hashes/order, participants, private-state ownership, progression, consent/capacity/pressure/freedom, operational observable codes, and prohibited inferences—never exact protected prose;
- synchronized exact Composer envelope and safe Reasoner ledger with shared unit identity, hash, order, progression state, participants, source/snapshot/authority bindings, and a domain-separated synchronization receipt;
- exact protected source absent from Reasoner requests, evidence indexes, mechanics requests/proposals, general receipts, diagnostics, and validation records;
- re-fetch of adult identity/authority and semantic/content-trigger evidence under the same immutable snapshot;
- stateless current-turn `AdultContextSelector` with typed abstract craft-family/reference IDs only, no actual examples or vocabulary library;
- private suspicion, historical context, or body-state-only triggers rejected from craft activation; a private suspicion cannot activate infidelity-drama context;
- ordinary-turn context recomputed as inactive and empty, preventing adult reference/profile/family leakage from a prior turn or provider conversation;
- provider-neutral `AdultMechanicsPort` and production-prohibited declarative `FakeAdultMechanicsPort`;
- mechanics proposals limited to source-unit treatment, progression, participants, already-selected craft references, source-outcome preservation, and no-adjacent-psychology acknowledgement;
- mechanics proposal rejection for reasoner decision/SequencePlan drift, source-unit omission/order changes, participant or progression changes, unselected craft context, exact-source leakage, story prose, or psychology override;
- valid and malformed adult advisory metadata separated into retained non-durable and quarantined records;
- `AdultComposerBinding` carrying authority, synchronization receipt, safe/exact view, context, validated mechanics proposal, mechanics receipt, and craft-reference hashes/IDs;
- the existing `SceneComposerRequest` now requires that adult binding for a protected route and forbids it on an ordinary route;
- full synthetic authority -> Reasoner -> mechanics -> Composer -> in-memory accepted-artifact tests using the existing Phase 5 and Phase 6 validation boundaries;
- no alternate adult publication path, no direct raw Composer return, no model-owned durable state, no generation advance, and no database dependency from the adult route.

## Architectural correction

The earlier phrase “DeepSeek Adult Planner” could imply a second owner of intent or psychology. Phase 7 narrows that role to a future `AdultMechanicsPort` adapter:

- Scene Reasoner owns intent, tactic, responder selection, floor, uncertainty, relationship meaning, and consequence direction.
- Python owns adult authority, synchronization, context selection, and validation.
- Adult Mechanics may only enrich operational protected mechanics within the already validated decision and selected craft context.
- Scene Composer owns complete prose realization and remains constrained by the validated decision.

The mechanics adapter receives the safe causal ledger, decision hashes, and abstract craft references. Exact protected prose remains restricted to the Composer envelope.

## Verification

Repository validation passed before this report:

- Python bytecode compilation: pass;
- deterministic unit tests: **105/105 pass**;
- documentation validation: pass with zero findings.

Phase 7 adds 15 focused tests covering:

- complete deterministic synthetic adult route through existing Reasoner and Composer acceptance with no writes;
- unknown adulthood, absence, withdrawal, impaired capacity, pressure, and limited freedom failures before model stages;
- no age inference for an unrecorded participant;
- consensual roleplay versus actual non-consent, withdrawn consent, and uncertain/absent consent;
- source-authored bodily response without adjacent desire/pleasure/relationship inference;
- safe/exact source tamper, omission, order, progression, and participant mismatch failures;
- exact protected text absence from reasoner/receipt/index paths and inability to fetch a protected-source ID through evidence tools;
- current typed craft selection, private-suspicion rejection, and zero ordinary-turn adult context;
- unsupported content-family failure without substitution;
- stale snapshot and evidence-service outage with no fallback;
- mechanics decision/unit/participant/craft/psychology/prose drift rejection;
- advisory quarantine without commit;
- exact protected text leak through mechanics advisory rejection;
- production fake prohibition and explicit unavailable error;
- synthetic/noncanonical authority, no examples, and unqualified provider status.

## Deliberately not done

- no live `DeepSeekAdultMechanicsPort`, `DeepSeekSceneComposerPort`, or `CodexSceneReasonerPort`;
- no provider credentials, endpoints, model calls, retries, repairs, Detailer, or fallback;
- no real Genesis, character identity/profile, creator adult authority, actual Adult EX prompt/example, or character-specific adult material;
- no provider/content-family qualification or prose-quality claim;
- no event/memory/relationship/material integration, commit, generation advance, publication, SillyTavern, external handler, deployment, or production database;
- no blocked-event generation or external-handler behavior.

## Review result and blocker-boundary interpretation

Pro initially requested a rework that would have routed a creator-authored actual-nonconsensual event through the exact Composer path. Codex rejected that recommendation because it contradicted D-013, D-015, D-018, and the controlling blocked-turn/resumption contract. Pro re-evaluated and returned `PHASE_7_ACCEPTED_INFRASTRUCTURE_COMPLETE`, explicitly retracting the conflicting recommendation.

The accepted interpretation is:

```text
current or requested blocked event
-> checkpoint/rejection
-> no Scene Reasoner, Adult Mechanics, or Scene Composer through the event
```

```text
validated neutral external completion receipt
-> non-graphic established-event semantics
-> Reasoner and Composer resume for aftermath only
```

```text
already-ended past harm supplied as a new aftermath premise
-> non-graphic established past fact
-> current aftermath reasoning/composition only
-> no event re-realization and no Adult Mechanics for the past event
```

`actual_nonconsensual` therefore means ineligible for the Phase 7 adult realization route. It does not erase or retcon historical harm, convert it to consent, or authorize exact Composer realization. Blocked-event receipt/resumption and past-fact ingestion remain separately authorized later work.
