# Phase 14 DeepSeek Scene Composer Adapter Result

**Date:** 2026-07-28  
**Status:** accepted by ChatGPT Pro within the provider-free adapter boundary  
**Authorization:** D-065  
**Scope:** typed DeepSeek Scene Composer packet/result mapping, bounded realization context, one-shot transport integration, provider-neutral receipts, and deterministic non-live adapter tests

## Outcome

CERA now has an executable `DeepSeekSceneComposerPort` that joins the accepted one-shot DeepSeek transport to the provider-neutral Composer contracts and existing Python structural validator.

For one invocation it:

1. requires a bounded `ComposerRealizationContext` before any provider call;
2. deterministically serializes `cera.deepseek_scene_composer_packet.v1`;
3. binds the Composer request, validated decision, SequencePlan, and context hashes;
4. includes exact current source only at the Composer boundary, including the restricted envelope only for a consent-valid protected route;
5. supplies selected-character identity/voice evidence and only explicitly selected craft modules;
6. makes exactly one DeepSeek JSON-object call with thinking disabled and no retry/fallback;
7. strictly decodes `cera.deepseek_scene_composer_response.v1`;
8. resolves provider quote anchors to exact story offsets in Python;
9. generates candidate/manifest IDs and all story/hash bindings in Python;
10. reuses Python's complete-Core, cast, floor, move, beat, source-state, protected-user, semantic-inference, and boundary validation;
11. returns an in-memory accepted presentation-neutral artifact without a story-state write.

DeepSeek creates the complete prose candidate. It does not select participants, re-decide psychology, change the floor, commit future segments, authorize adult content, create durable memory, write the database, or decide that its own output is accepted.

## Realization context

The former Composer request contained a validated decision and source but no executable character/voice context. That was insufficient for the user's stated quality requirement and for the controlling prompt architecture.

Phase 14 adds typed context blocks for:

- character identity and voice;
- current state, relationship, continuity, scene, and material facts;
- creator craft references.

Exact context must be evidence already hard-cited by the Reasoner or explicitly selected as a continuity reference. The request rechecks Genesis revision, owner-private scope, knowledge owners, selected-character applicability, content class, byte budget, and duplicate IDs. Craft is labeled separately, teaches technique rather than facts, and on an adult route must exactly match the adult binding's selected craft IDs. Ordinary routes reject protected-adult craft.

The live-shaped adapter refuses to call DeepSeek unless every selected NPC has at least one bounded identity or voice block. This avoids asking the prose model to infer character identity from an action-only plan. Context assembly and evidence selection remain Python-owned; DeepSeek has no retrieval or database tools.

## Provider response boundary

DeepSeek returns complete story prose plus declarative realization metadata. It copies the validated move hashes and beat IDs and supplies exact quote anchors with zero-based occurrence values for source coverage and character spans.

DeepSeek is not asked to compute SHA-256 values, deterministic IDs, or character offsets. Python resolves quote anchors, rejects absent occurrences, computes candidate/manifest IDs and hashes, and performs the authoritative structural validation. Provider flags and manifest claims are untrusted until that validation succeeds.

The closed response schema rejects unknown fields, bounds arrays, constrains typed-ID kinds and enum values, and keeps advisory notes separate. Advisory notes remain quarantinable metadata rather than truth.

## Adult boundary

For an ordinary route, the packet carries ordinary exact source and rejects protected-adult craft. For a consent-valid protected route, the Composer may receive the exact restricted source envelope because prose realization is its assigned role. Runtime Codex still receives only the separate non-graphic safe ledger. The adapter prompt explicitly states that bodily response, vocalization, freezing, silence, compliance, or failure to resist never establishes desire, pleasure, or consent.

No Adult EX material was imported or used. No protected story content was sent to a provider. Deterministic tests use synthetic markers only.

## Failure behavior

- Missing character/voice context fails before a provider call.
- Provider unavailability produces `CERA_COMPOSER_UNAVAILABLE` after one attempt.
- Malformed/unknown response fields, invalid typed values, and unresolved quote anchors produce `CERA_COMPOSER_CONTRACT_INVALID`.
- Decision, floor, cast, move, beat, source-state, or protected-user drift is rejected by the existing Python coordinator.
- There is no automatic retry, alternate model, silent fallback, partial acceptance, story write, or memory update.

The first complete regression run exposed one old aftermath caller that still expected the former direct `ComposerSubmission` return. It failed before state mutation. The caller was migrated to consume the provider-neutral adapter-call envelope and v2 receipt fields; the focused restart/atomic-resumption test and full suite then passed.

## Deterministic coverage

The nine new adapter tests cover:

- no-call rejection when realization context is missing;
- deterministic evidence-bounded packet/schema construction;
- one-call ordinary candidate acceptance with safe receipt retention and zero writes;
- multi-character cast preservation;
- quote-anchor-to-offset conversion and invalid-anchor rejection;
- Python rejection of move-hash, floor, and beat drift;
- explicit unretried provider failure;
- exact protected Composer envelope versus safe Reasoner ledger separation and ordinary-route craft rejection;
- unknown provider-field rejection.

Existing Composer, adult-route, blocked-resumption, real-Genesis, branch, restart, privacy, and transaction tests continue to pass.

## Validation

```text
python -m unittest tests.test_deepseek_scene_composer -v
Ran 9 tests
OK

python -m unittest discover -s tests -p "test_*.py"
Ran 225 tests
OK (skipped=1 optional live probe)
```

`compileall` completed cleanly. The suite used fake DeepSeek HTTP responses only; Phase 14 made no provider call.

## Deliberate non-claims

Phase 14 does not prove:

- live DeepSeek acceptance of the complete packet or response reliability;
- prose quality, character voice, buildup, multi-scene pacing, or adult realization quality;
- Codex Reasoner judgment or end-to-end model interaction;
- context-selection recall against the full real Genesis;
- Adult EX selection or Adult Mechanics provider quality;
- publication, durable turn commit, production binding, or SillyTavern readiness;
- route promotion or final creator acceptance.

## Next gate

After Pro review, the next safe milestone is a provider-free live-shaped pipeline integration: construct bounded Composer realization cards from authorized exact evidence, pass fake Codex and DeepSeek transport outputs through the typed adapters, and verify the complete turn remains branch-safe, restart-safe, receipt-complete, and uncommitted until one atomic transaction.

Any live story/Genesis/adult role call, role-quality calibration, human ballot set, Adult EX import, production database/world binding, SillyTavern integration, route promotion, or deployment remains separately gated.

## ChatGPT Pro review

ChatGPT Pro returned exactly `PHASE_14_DEEPSEEK_SCENE_COMPOSER_ADAPTER_ACCEPTED` with no in-scope correction. The verdict accepts the ownership split, evidence/craft context boundary, fail-before-call behavior, Python-owned quote-anchor conversion/identity/hashes, Composer-only exact protected envelope, one-call/no-retry receipt semantics, and bounded non-claims. It does not qualify live DeepSeek prose, Codex reasoning, end-to-end semantic quality, any protected-content route, production binding, publication, promotion, or deployment.
