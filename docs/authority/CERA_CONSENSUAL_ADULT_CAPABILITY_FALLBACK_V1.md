# CERA Consensual Adult Capability-Fallback Route V1

**Status:** controlling deferred architecture decision; not implementation or live-call authority  
**Version:** `cera.consensual_adult_capability_fallback.v1`  
**Date:** 2026-08-03  
**Owner:** Ted  
**Manager:** ChatGPT Pro repository-review thread

## 1. Scope

This route applies only when Python has already established that every involved character is an adult, present consent and capacity are valid, freedom to stop remains intact, and the requested material is otherwise within the creator-authorized adult route, but the selected Codex Planner adapter reaches a provider capability restriction.

It is not the non-consent blocked-event route and must never be used to relabel refusal, coercion, incapacity, uncertainty, or prohibited material as consensual. Any failure of adult identity, consent, capacity, or freedom uses the controlling blocked-turn architecture instead.

A generic exception is not story authority. The Codex adapter converts a recognized capability refusal into a typed, privacy-safe `capability_restricted_at_boundary` receipt only after Python has independently established the consent-valid adult route.

## 2. Deferred runtime flow

```text
User prompt
-> Python exact ingress, adult identity, consent/capacity, branch, and evidence authority
-> Codex Planner
   -> normal supported plan: return to the normal CERA route
   -> capability_restricted_at_boundary:
        Codex boundary sequence materials + character package + handoff prompt
-> Python freezes one RestrictedAdultBoundaryPackage
-> DeepSeek Planner
-> DeepSeek Writer
-> DeepSeek Safe-Continuity Formatter
-> Python mechanical and authority validation of the safe package
-> Codex Validator reads only the safe package and pre-boundary authority
-> Reader/creator review
-> Python Memory / Directory / branch-state commit from accepted safe fields only
```

The first typed restriction opens one bounded restricted interval. CERA does not repeatedly spend Codex calls to obtain the same error. DeepSeek owns the restricted interval until its safe Formatter declares that interval complete or the route terminates. Codex resumes only from the accepted safe continuity package.

## 3. Codex boundary package

Codex may reason only through the material it can handle. At the boundary it produces a safe handoff containing:

- the validated sequence materials through the last supported point;
- active NPC cast and the protected-user stopping boundary;
- relevant character package sections;
- owner-specific feelings, motives, knowledge, and expectations established before the restriction;
- current location, positioning, material situation, relationship state, and unresolved threads;
- exact consent/capacity status and invariants that may not be reinterpreted;
- the last allowed public scene state;
- open realization requirements and continuity constraints;
- a bounded `DeepSeekScenePrompt` describing the scene objective without inventing unsupported facts.

Python attaches the exact creator source and authorized adult craft/context directly to the DeepSeek Planner. Codex is not required to restate material that caused its capability restriction.

The boundary package is advisory context, not permission to alter consent, protected-user authority, cast, branch truth, or character knowledge.

## 4. DeepSeek restricted interval

### DeepSeek Planner

Receives the frozen boundary package, exact creator source, authorized character material, and adult craft scope. It creates a scene plan for the restricted interval only. It cannot write memory, events, relationship changes, or canonical state.

### DeepSeek Writer

Receives one accepted DeepSeek plan and writes the visible scene. It remains a prose generator, not a story-authority component.

### DeepSeek Safe-Continuity Formatter

Receives the exact Writer output and produces a retained, non-graphic continuity package containing only what ordinary CERA needs to resume:

- ordered neutral event milestones;
- owner-specific character feelings and interpretations;
- character knowledge changes;
- consent/capacity status and any changes;
- relationship implications as proposals;
- material, location, condition, injury, safety, and ending-state facts or explicit unknowns;
- active participants and observers;
- unresolved threads and the exact resumption point.

It must omit explicit prose and unnecessary detail. Its output is a claim, not proof that it perfectly represents the hidden Writer output.

## 5. Authority and retention

The raw restricted-interval prose is never sent to Codex Validator. It may be presented to the creator as a provisional restricted-route response, but it is not independently Codex-validated semantic authority and must not be used as retrieval evidence, memory authority, or a source for automatic durable updates.

Only the Safe-Continuity Formatter package may cross back into the normal CERA authority path. Python validates its schema, identity, hashes, required fields, chronology, consent invariants, explicit unknowns, and non-graphic boundary. Codex then validates only the safe package against the frozen pre-boundary state. Creator acceptance remains required before Python commits Memory / Directory / event / relationship / material updates.

Codex cannot certify fidelity to raw prose it did not inspect. CERA must retain that limitation in receipts and user-visible route status.

## 6. Bounded DeepSeek recall policy

DeepSeek variability is handled by bounded fresh attempts. Python defects are not.

- Every DeepSeek stage has a maximum of **three total provider attempts**, not three retries after the first.
- The normal CERA Writer therefore has at most three Writer attempts for one frozen turn package.
- This deferred restricted route has at most three Planner attempts, three Writer attempts, and three Formatter attempts: nine DeepSeek calls in the absolute worst case.
- Every attempt has a fresh immutable attempt/candidate ID and receipt while retaining the exact same frozen stage input, model route, prompt/schema version, and authority package.
- Outputs from different attempts are never merged, patched together, or treated as one candidate.
- The first attempt that passes its stage gate becomes the sole downstream input; all other attempts remain rejected evidence outside accepted ancestry.
- Retryable classes may include confirmed provider failure, empty/truncated output, malformed minimal wire, deterministic schema failure, or a downstream rejection specifically attributable to that DeepSeek stage.
- A Python exception, contradictory deterministic result, authority failure, branch conflict, consent/capacity failure, or ambiguous provider-submission state is not repaired by another DeepSeek call. Python stops and records the defect; an ambiguous submitted call still consumes an attempt.
- If all three attempts for one stage fail, the route ends with no Memory / Directory / branch commit.

For a Formatter rejection, CERA retries the Formatter against the same frozen Writer bytes before regenerating the Writer. Regenerating an earlier stage requires a typed reason showing that the earlier accepted stage omitted information required by its contract.

## 7. Testing boundary

This route is not implemented during Queue 0044. Its interfaces may later be tested with ordinary allowed material that simulates a capability-restricted interval. Benign tests must prove:

- exact boundary-package custody;
- sticky restricted-interval routing without repeated Codex errors;
- three-attempt ceilings and call accounting;
- no cross-attempt merging;
- safe Formatter chronology, owner-specific state, material changes, ending state, and explicit unknowns;
- no explicit leakage into the Codex-facing package;
- no raw restricted prose entering Memory / Directory authority;
- restart, branch, regeneration, failure, and no-mutation behavior.

Benign substitute tests qualify the handoff and retention mechanism only. They do not claim that untested content classes were semantically validated.

## 8. Current authority effect

This document records the future CERA format requested by Ted. It does not authorize provider calls, implementation during Runtime Model V3 Stages 0-3, active-route changes, production use, SillyTavern activation, story mutation, or deployment. A later manager queue must explicitly open this route.
