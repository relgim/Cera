# Native Stored Reasoner Canary Result

**Decision:** D-176  
**Date:** 2026-07-31  
**Status:** terminal pre-provider failure; active route unchanged

## Result

The separately authorized minimal native-stored Reasoner canary stopped before
provider dispatch. The disposable session ledger contained a non-ephemeral
root identity, but that identity did not correspond to a provider-persisted
rollout. Both resume and bounded cleanup therefore returned `no rollout found
for thread id`.

This is a lifecycle-initialization defect, not a Sol model, prompt, schema, or
MCP result. CERA must create and prove the provider-stored root before treating
it as resumable or forkable.

## Evidence

- Evidence: `evaluation/evidence/native_stored_reasoner_live_five_2026-07-31_v1/canary.json`
- SHA-256: `8cec41869b7a02303883aa8c21269255252912ebdf74bf33ccbf8e4542861a73`
- Wall time: 1.621 seconds
- Provider calls: 0
- Retries/fallbacks: 0/0
- Story commits: 0
- Five-case batch: not started

The active Sol-medium SillyTavern route was not changed. Another live attempt
requires a provider-free lifecycle correction and separate authorization.
