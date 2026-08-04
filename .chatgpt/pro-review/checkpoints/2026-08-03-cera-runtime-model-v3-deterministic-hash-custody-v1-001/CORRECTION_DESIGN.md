# CERA Runtime Model V3 deterministic hash-custody correction

Checkpoint: `2026-08-03-cera-runtime-model-v3-deterministic-hash-custody-v1-001`

Queue: `0049`

Provider calls authorized for this checkpoint: `0` Codex, `0` DeepSeek.

## Reconciled starting state

- Execution source: Git `0d5a2af9b31ab67a2ff62b3138c4374789a25745`, tree `b5a428d87c9932aff681ada59630a49d00093748`, tracked-clean before this correction.
- Queue 0048 terminal result SHA-256: `50134e5fd8242fab7238ada26517baa5015f0f05afa9911883ad701c9e5a69e2`.
- Queue 0048 terminal report SHA-256: `79729780f42d36811b677a716c640be35386ef41b5f6a4d79135d96d80ad4387`.
- Queue 0048 failed-attempt SHA-256: `07ca518a81d290c0c460cc90115f82035d8c32c5a40cc8211af126279573ecdf`.
- Queue 0048 provider ledger SHA-256: `d5a247b140a6baa6e239cce1ae929d28d589f9af34ae99d20a1d0d265567dbaa`.
- Queue 0048 raw provider result SHA-256: `d872a56484631b9f138fdf2d6e3b80279e83610e3de4f64fb285fceca3000256`.
- Queue 0048 used three Codex-family calls and zero DeepSeek-family calls; protected effects were unchanged.
- Original immutable Stage 4B Writer result SHA-256: `1a18a47eb4a9a6ad523f8d651d6450aa637adf39f9a9a5d207dfd70e1ad52abd`, recording five of five first-attempt mechanical passes.

All prior V1-V3 live and correction identities remain terminal and all historical schemas and provider evidence remain unchanged.

## Root cause and adjacent audit finding

The active Validator and Reader output contracts required models to calculate hashes over exact text that Python already possessed. Empty or invented values therefore failed after a paid provider result even when the semantic span work was usable. The recursive active-schema audit also found `expected_prior_value_sha256` nested under persistence directives. That hash is deterministic bookkeeping over the exact ACTIVE value and likewise cannot be model-owned.

The only remaining active provider-output hash is `persistence_policy_sha256`. It is an immutable Python-supplied policy identity and is constrained to its exact `const` in both the neutral and OpenAI-projected schemas.

## Focused design

1. Add a fresh hash-free provider adjudication DTO. Codex selects the segment, offsets, relation, NPC assertion owners, and protected-user claim references.
2. Pass the immutable Writer text to the Validator adapter as a typed runtime argument independent of prompt parsing. Python proves the cited segment and subspan against those bytes and derives the canonical adjudication hash.
3. Add fresh hash-free Reader verdict and issue DTOs. The Reader selects verdict, scores, reason codes, issue spans, and explanations. Python derives the full-story and per-issue hashes from the typed Writer text.
4. Omit `expected_prior_value_sha256` from the active Validator provider schema. After raw-provider capture and before canonical DTO decode, Python injects `null` for add or derives the exact replace prior-value hash from the branch's bounded `ACTIVE` file and JSON pointer. Provider-authored values fail closed.
5. Preserve the canonical and historical DTOs unchanged as immutable evidence readers.
6. Advance only changed active compatibility identities: Semantic Validator draft V7, Validator adapter V15/prompt V16/request V6, Reader provider verdict V1, Reader adapter V2/prompt V2/request V2. Writer identities remain unchanged.
7. Keep the call ledger terminalization and raw provider capture before deterministic compilation. No retry, fallback, output merge, repair, or provider call is added.

## Verification boundary

The frozen candidate must pass schema preflight for both neutral and final OpenAI surfaces; adversarial schema and typed-decode tests; all accepted/concern/rejected/inconclusive/error Validator branches; all Reader verdict branches; invalid span and altered-text cases; raw-capture and ledger terminalization; historical-reader checks; Writer-surface binding; compilation; focused affected tests; diff checks; and one complete repository suite using the repository's existing read-only dependency environment.

No provider dispatch is permitted before a passing local commit freezes this checkpoint.
