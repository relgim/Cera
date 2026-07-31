# Structural Contract v2 Five-Run Live Qualification V3 Result

**Date:** 2026-07-29  
**Status:** terminal 0/5 at Codex response-schema handshake; no model output was produced  
**Authority:** D-117 and D-118

## Authorized boundary

The creator authorized one fresh five-case live qualification after Structural
Contract v2. The runner used new request/session/idempotency identities and a
new immutable evidence directory. V1 and v2 were not overwritten or resumed.

```text
qualification_id: five-run-live-v3-structural-contract-v2
evidence: evaluation/evidence/live_story_qualification_2026-07-29_v3_structural_contract_v2/
duration_seconds: 43.314
attempted: 5
passed: 0
failed: 5
automatic_retry_count: 0
fallback_enabled: false
story_state_committed: false
story_authority_writes: 0
summary_sha256: FF557F2B31434DB75E1540D6ED2D01A87EBA688B9011CD59620A62AE454ADD17
```

The preserved v1 and v2 summary hashes remain:

```text
v1: 2154410A40906FA9DAF67F20E4FF6242CEE058B7846BF2EF744FABA04F624A02
v2: 61D93A90AB2CF17ABD3AB31CD688C861703152BD5EBDCBFFDA4D5A5999D7F376
```

## Terminal result

All five cases reached the same provider-side schema rejection before Codex
reasoning:

```text
invalid_json_schema
adult_craft_need.anyOf[0].beat_requirements.items
  .channel_requirements.items.oneOf is not permitted
```

This means:

- five Codex transport dispatches reached response-format validation;
- zero Codex reasoning outputs were produced;
- zero DeepSeek calls occurred;
- retrieval, composition, realization verification, and prose quality were not
  exercised;
- no provider receipt was created because the request failed before model
  execution;
- no story or derived state was committed.

## Root cause

`AdultCraftNeedV2` uses a correct JSON Schema discriminated union for channel
ownership. The provider-free schema/domain differential tests validate that
contract against a general JSON Schema implementation. The live Codex
structured-output endpoint accepts only a restricted schema dialect and
rejects nested `oneOf` at this location.

The missing architecture layer is provider-dialect qualification/projection:
the authoritative domain/schema can remain expressive, but the Codex adapter
must submit a provider-supported response schema and let Python enforce the
full cross-field discriminated-union semantics after decoding. This is a
shared provider-compatibility defect, not five separate case failures and not
evidence about Reasoner judgment or Composer prose.

## Harness and evidence notes

Before the run, the qualification harness was advanced to the active v2 seams:

- Python seed reauthorization and seed receipts;
- `CodexReasonerDraftV2` and minimal DeepSeek draft contracts;
- independent echo-accepting scripted realization verification, explicitly
  labeled non-proving;
- corrected live receipt names and privacy-safe failure receipt fields;
- fresh batch-bound request, session, and idempotency identities.

The updated harness passed 44 focused Structural v2, Reasoner, Composer, and
pipeline tests before live dispatch.

## Next gate

Do not rerun v3. The next safe action requires creator authorization for a
provider-free Codex response-schema dialect correction and tests that exercise
the exact schema accepted by the live transport. Only after that correction
passes offline review should another fresh live batch be authorized.

This result grants no retry, fallback, route promotion, story publication,
production binding, SillyTavern activation, handler work, or deployment.
