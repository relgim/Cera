# Structural Contract v3 Provider-Free Result

**Date:** 2026-07-29  
**Authority:** D-138  
**Status:** implementation, deterministic verification, and advisory review complete

## Outcome

The four shared failure classes exposed by terminal v5 are corrected without
editing or retrying any v1-v5 qualification evidence. This work made no live
provider call, created no persistent story world, published no story content,
and did not activate SillyTavern or port 5101.

The active provider-free path is now:

```text
CodexReasonerDraftV3
-> Python ReasonerOutcome v4 / AdultCraftNeedV3
-> deterministic craft selection
-> DeepSeekCompositionDraftV3 ordered story segments
-> Python concatenation, offsets, spans, and manifest
-> structural and independent semantic verification
-> accepted in memory or TurnFailureEvidenceBundleV2
```

## Shared corrections

### Semantic versus lexical adult craft ownership

`AdultCraftNeedV3` separates each channel's `semantic_concepts` from its
`lexical_concepts`. Semantic concepts select craft technique and semantic
verification but do not create exact-word requirements. Lexical concepts are
an explicit duplicate-free subset and alone generate `lexicon:*` catalog
requirements and `SpecificityTermRequirement` entries.

The active Codex provider DTO and prompt advance to v3/v6. Historical draft v2
and AdultCraftNeed v1/v2 remain registered and decodable; no historical
qualification artifact was rewritten.

### Python-owned Composer anchoring

`DeepSeekCompositionDraftV3` replaces provider-copied quote anchors with:

- ordered local-keyed prose segments;
- source-unit-to-segment references;
- participant/beat-to-segment references;
- one terminal segment key.

Python strips outer segment whitespace, joins segments with exactly two
newlines, and derives candidate text, offsets, hashes, participant projections,
and realization spans. Unknown segment keys, wrong source order, unknown beats
or owners, missing participants, incomplete beats, and invalid terminal order
still fail closed. DeepSeek remains creative rather than authoritative.

### Universal safe failure evidence

`TurnFailureEvidenceBundleV2` retains the existing typed handles plus canonical
payloads only for an explicit allow-list of receipt schemas. The wrapper rejects
noncanonical JSON, schema substitution, handle/hash mismatch, and protected
fields such as raw source, story prose, prompt, provider output, private
evidence, or secrets.

Reasoner, adult-craft selection, context assembly, Composer, adult specificity,
repair, and realization-verifier terminal paths export all privacy-safe receipts
crossed before failure. SQLite's append-only migration-11 tables require no
destructive migration because each row already stores a versioned canonical
bundle JSON object. The store decodes both v1 and v2.

### Separately governed rejected-candidate review

`RejectedCandidateReviewPort` is opt-in and qualification-only. When installed,
it can retain bounded creator-local excerpts around Python-validated verifier
findings. Each record is branch/generation/candidate bound, non-authoritative,
pending creator adjudication, and manually deleted after review.

The artifact is deliberately excluded from the general runtime schema registry,
story storage, and telemetry. Runtime failure evidence stores only its ID/hash.
With no installed review port, rejected candidate prose is not retained.

## Versioning and compatibility

- active Codex adapter/packet/prompt: v6;
- active `cera.codex_reasoner_draft.v3`;
- active `cera.reasoner_outcome.v4`;
- active `cera.adult_craft_need.v3`;
- active DeepSeek adapter/packet/prompt: v6;
- active `cera.deepseek_composition_draft.v3`;
- active `cera.turn_failure_evidence_bundle.v2`;
- historical v2 DTOs, AdultCraftNeed v1/v2, ReasonerOutcome v3, and failure
  bundle v1 remain registered decoders.

The active provider-schema inventory now projects the v3 Reasoner and Composer
DTOs. OpenAI's disjoint channel-owner union remains dialect-projected and
recursively preflighted; DeepSeek's schema remains prompt guidance followed by
unchanged strict Python decoding.

## Verification

Clean pre-change baseline:

```text
331 tests passed
```

Focused Structural Contract v3 and AdultCraft regression:

```text
24 tests passed
```

Complete provider-free suite after implementation:

```text
339 tests passed in 214.692 seconds
```

Coverage includes:

- semantic-only versus lexical-required craft selection;
- lexical-subset cross-field rejection;
- provider dialect projection and domain differential validation;
- Python segment concatenation and span derivation;
- unknown segment/reference adversarial output;
- adult pre-Composer failure evidence export;
- allow-list and protected-content mutation rejection;
- creator-local rejected-candidate retention with prose-free runtime receipts;
- historical v2 adapter compatibility;
- existing privacy, branch, retrieval, restart, adult, Genesis, and 20-run
  provider-free matrices.

## Boundaries and next gate

This result does not qualify any live story role and does not change terminal
v5's 1/5 result. No provider schema probe or five-case batch was run.

The installed `Hanezawa Family - Cera v1.0` card/profile remains intentionally
not chat-ready. The product adapter is a separate implementation gate: no
OpenAI-compatible server is listening at `127.0.0.1:5101`, no persistent world
is bound, and no SillyTavern activation or deployment occurred.

## Advisory closure

ChatGPT Pro returned the exact verdict
`CERA_STRUCTURAL_CONTRACT_V3_PROVIDER_FREE_ACCEPTED` with no required
correction. Codex independently checked the advisory conditions against the
actual source rather than treating the external verdict as repository proof.
The active Composer prompt and DTO require ordered final-prose segments,
Python-owned concatenation, segment-key references instead of copied anchors,
and no model-owned semantic authority. The receipt and rejected-candidate
review contracts remain fail-closed, prose-separated, and noncanonical.

This closes D-138 only. It grants no live qualification, product-adapter
implementation or activation, persistent world, SillyTavern human test,
publication, promotion, handler, or deployment authority.
