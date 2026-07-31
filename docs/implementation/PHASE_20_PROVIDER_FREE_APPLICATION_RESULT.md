# Phase 20 Provider-Free Ordinary Application Result

**Date:** 2026-07-28  
**Status:** accepted by ChatGPT Pro; provider-free ordinary path complete  
**Authorization:** standing provider-free continuation after Phase 19 acceptance  
**Scope:** one production-prohibited, SillyTavern-neutral ordinary-turn application facade using fake transports and disposable databases

## Outcome

CERA now has one typed entry point over the complete provider-free ordinary workflow:

```text
OrdinaryApplicationRequest
-> exact committed-request replay check
-> LiveShapedTurnPipeline
   -> bounded evidence
   -> typed Reasoner adapter
   -> Python decision validation
   -> bounded Composer context
   -> typed Composer adapter
   -> Python artifact validation
-> atomic story/event/work publication
-> post-publication dispatch
-> secret-safe operational report
-> OrdinaryApplicationResult
```

`ProviderFreeOrdinaryApplication` is rejected in `Environment.PRODUCTION`. It accepts only a prepared ordinary `SceneReasonerRequest` plus ordinary `ComposerRequestPlan`; adult bindings and non-ordinary routes fail contract validation.

## Application contracts

`cera.ordinary_application_request.v1` binds the prepared Reasoner request, Composer plan, optional renderer profile, and whether derived consolidation is requested. The request hash is the top-level application correlation boundary.

`cera.ordinary_application_receipt.v1` binds:

- application request hash and turn identity;
- accepted artifact/commit/transaction identity and hashes;
- secret-safe Phase 19 operational report identity and hash;
- adapter-call count for the newly executed provider-free shaped path;
- exact-replay status;
- downstream failure count;
- `story_state_committed: true` and `adult_route_activated: false`.

The result contains the final accepted presentation-neutral artifact, its commit receipt, the safe operational report, and the application receipt. It does not return provider packets, evidence, prompts, internal rendering output, consolidator proposals, or reasoning internals.

Both public schemas are registered in CERA's schema registry.

## Exact replay before adapters

Before calling the Reasoner, the application searches the authority store by request ID, branch, idempotency key, and protected source hash. If an exact committed turn exists, it verifies that the immutable post-publication work plan matches the current application request, then returns the stored artifact/commit/report.

Exact replay:

- makes zero Reasoner or Composer calls;
- makes zero renderer or consolidator calls;
- does not require those adapters to be available;
- does not dispatch pending work or retry failed work;
- reports `adapter_call_count: 0` and `exact_replay: true`.

Pending or failed downstream work remains under the explicit Phase 19 operator commands. This prevents an ordinary request replay from becoming a hidden retry mechanism.

## Failure boundaries

Missing requested downstream adapters fail application preflight before reasoning or writes. Reasoner/Composer failures carrying an existing `ErrorEnvelope` are normalized to `OrdinaryApplicationFailure` without changing their stable code. Unexpected pre-publication pipeline failures become a generic verifier failure. Story transaction conflicts/rollback become stable application publication errors.

Renderer or consolidator failure occurs after accepted publication and is not raised as a failed story turn. The application returns the accepted artifact plus the safe operational report showing failed/pending downstream work. Replaying that request still does not retry it.

## Branch behavior

The application preserves the existing immutable regeneration contract. A replacement is a sibling of the replaced head. A branch fork created before regeneration retains the prior accepted artifact while the parent branch advances to the replacement. Application requests with no post-publication consumers produce an empty but valid operational report.

## Deterministic evidence

Seven provider-free application cases cover:

1. complete ordinary fake-transport reasoning/composition/publication/render/no-change/report flow and schema decoding;
2. exact replay with every adapter unavailable and zero new calls;
3. downstream renderer/consolidator failure returned with committed story and sanitized report, plus replay without retry;
4. regeneration through the application facade while a prior fork retains the old artifact;
5. missing requested downstream adapter rejected before Reasoner/Composer calls or writes;
6. Composer/pipeline failure normalized to the application failure contract with no story/event commit.
7. conflicting reuse of a committed idempotency key rejected before any adapter call while the original artifact remains intact.

The complete repository suite passes:

```text
python -m unittest discover -s tests -p "test_*.py"
Ran 255 tests
OK (skipped=1 optional live probe)
```

`compileall` and documentation validation pass. Actual provider/network calls: zero. All story, consolidation, and operational writes use auto-deleting development databases.

## Deliberate non-claims

Phase 20 does not establish:

- live Codex or DeepSeek story-role quality/reliability;
- semantic realism, prose quality, multi-scene quality, or human acceptance;
- adult publication, Adult EX retrieval, or adult-memory behavior;
- a raw-user-message intake facade; input begins at the already prepared typed Reasoner request;
- CLI, HTTP, SillyTavern, external-handler, or other product transport;
- production-world/database binding, authentication, scheduler/leasing, telemetry operations, promotion, or deployment.

## Authorization gate

This completes the broad provider-free ordinary integration path. Further useful work now crosses a separately closed boundary. Creator direction and explicit authorization are required to choose among:

1. bounded live story-role qualification for Codex and DeepSeek in disposable non-production worlds;
2. Adult EX material review/import and consent-valid adult provider-free application integration;
3. production-world/Genesis binding design;
4. SillyTavern-neutral transport contract and later SillyTavern integration;
5. production operational authentication/scheduling/deployment work.

None is implied by Phase 20 or Pro review.

## ChatGPT Pro review

ChatGPT Pro returned exactly `PHASE_20_PROVIDER_FREE_APPLICATION_ACCEPTED` and confirmed that the broad provider-free ordinary integration path is complete for its stated scope. Pro specifically accepted the pre-adapter identity-checked replay, zero-call/no-hidden-retry behavior, pre/post-commit failure separation, application receipt bindings, idempotency conflict handling, regeneration/fork lineage, ordinary-only routing, and non-exposure of internal packets/evidence/prompts.

Pro also confirmed that no further provider-free ordinary milestone is required. Further work must stop until the creator explicitly selects and authorizes one bounded next gate: live story-role qualification, Adult EX/adult-route integration, production-world binding, SillyTavern-neutral transport/integration, or production operations. The verdict authorizes none of those gates.
