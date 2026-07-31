# Provider-Free Adult ON/EX Catalog and Routing Result

**Date:** 2026-07-28  
**Status:** accepted; ChatGPT Pro returned `CERA_ADULT_ON_EX_PROVIDER_FREE_ACCEPTED` after the semantic-verifier correction  
**Authorization boundary:** provider-free fake adapters and disposable development databases only

## Outcome

CERA now has a repository-owned Adult ON/EX craft catalog and a deterministic, plan-derived routing path. The implementation does not activate a provider, publish adult prose, create production story data, bind a production world, integrate SillyTavern or an external handler, promote a route, or deploy anything.

The runtime sequence is:

```text
validated consent-valid adult SceneDecision
-> Codex-authored semantic AdultCraftNeed
-> Python catalog selection and SpecificityContract
-> selected character-card sections plus selected craft fragments
-> fake Composer result
-> beat-and-channel specificity validation
-> optional one-attempt beat-scoped repair
-> complete structural revalidation
-> transient uncommitted result
```

Blocked non-consensual generation cannot create an `AdultCraftNeed`, enter the selector, reach the Composer, or invoke repair.

## Source preservation and compilation

- Immutable provenance root: `adult/provenance/sera_adult_source_v1/`.
- Audited source artifacts: 24.
- Source-integrity SHA-256: `d2fce7f452e0c8149641bd573beee65904229fdc6bee82073b954818eb3f0d38`.
- CERA catalog root: `adult/catalog/adult_craft_v1/`.
- Catalog ID: `craft_catalog:cac0c3b1-7ed3-5d98-8f05-b6ca39101de4`.
- Compiled fragments: 82.
- Catalog-manifest domain SHA-256: `5bb4476d5dc2091a54af3c6941a1bb5d663024143d8e74503af784021c1a9767`.
- Runtime E-drive dependencies: zero.

All 24 sources remain byte-preserved. Curation classifies two sources as provenance-only, one coverage file as audit-only, and compiles the approved portions of the remaining sources at section granularity. Old Director/Detailer ownership, whole-library wrappers, and character-authority content are not imported as runtime authority. Coverage declarations are audit claims, never proof of output coverage.

## Implemented contracts

- `cera.adult_craft_fragment.v1`
- `cera.adult_craft_catalog_manifest.v1`
- `cera.adult_craft_need.v1`
- `cera.adult_craft_selection_receipt.v1`
- `cera.specificity_contract.v1`
- `cera.specificity_validation_receipt.v1`
- `cera.semantic_specificity_request.v1`
- `cera.semantic_specificity_result.v1`
- `cera.semantic_specificity_receipt.v1`
- `cera.beat_scoped_repair_request.v2`
- `cera.beat_scoped_repair_receipt.v1`
- `cera.reasoner_outcome.v2`
- `cera.scene_composer_request.v3`
- `cera.live_shaped_turn_receipt.v3`

The three evolved runtime contracts receive new versions because their serialized and hashed shapes now carry adult craft evidence. Earlier versions retain their historical meaning.

## Selection and realization behavior

- ON is a compact/direct route for simpler consent-valid adult realization.
- EX is the default for active explicit scenes unless the semantic plan proves the compact route sufficient.
- The selector performs deterministic coverage-within-budget selection. It has no fixed fragment count.
- Anal and toilet/scat are separate families and select disjoint family fragments.
- Direct/vulgar channel requirements require a compiled vocabulary group; a coverage label alone cannot satisfy specificity.
- Narration, each character's dialogue, each character's inner voice, sound effects, and physiology are distinct channels.
- Climax and aftermath each carry an authority state and a current-segment commitment. `Unresolved` never becomes `selected` by craft material.
- Character context is selected after cast/floor selection. Only requested sections of the selected character's `speech_system` record are supplied, with baseline voice retained; inactive characters are absent.

## Validation and repair

Specificity is checked inside the exact beat and channel where the plan requires it. No global word-count or quota heuristic is used. A missing indirect layer is not treated as a required explicit term unless the plan selected that layer.

Before a repair decision, the provider-neutral semantic-specificity verifier receives only locked beat-local text, the `SpecificityContract`, selected participant IDs, actor/action/object/material bindings, and required beat transitions. Its typed result can identify ambiguity despite correct vocabulary, wrong actor/action/object association, missing or reversed transitions, contradicted material outcomes, and false Composer coverage claims. Its privacy-safe receipt retains only hashes, adapter evidence, and counters. The scripted fake is declarative, makes zero provider calls, performs no prose analysis, and permits one invocation per request with no retry or fallback.

One repair is permitted only when exactly one beat fails either deterministic or semantic specificity. The request binds the failed beat ID, original span, original span hash, initial semantic-verifier receipt, locked non-target content, and original artifact. Python splices the returned replacement and rebases spans. A fresh post-repair semantic request must pass; then Python reruns the complete Composer structural validator. A post-repair semantic failure is terminal, cannot become a second repair, and commits nothing. The provider-free fake repair port is declarative and production-prohibited.

## Verification

- Adult craft and semantic-specificity focused suite: 15/15 passed.
- Contract and integration compatibility suite: 86/86 passed.
- Complete repository suite: 271 passed, 1 optional live probe skipped.
- `compileall`: passed.
- Deterministic catalog rebuild: byte-identical.
- Real Hanezawa Genesis/fake-adapter adult route: passed with zero provider calls, zero story writes, and no retained production database.

These tests establish deterministic contracts, selection, isolation, and fail-closed behavior. They do not establish DeepSeek prose quality, Codex semantic quality, provider reliability, route promotion, product readiness, or production safety.

## Pro review and acceptance

The first implementation review returned `CERA_ADULT_ON_EX_PROVIDER_FREE_CORRECTIONS_REQUIRED` because lexical beat/channel checks could not establish semantic association, transition order, material consistency, or false coverage claims. The provider-neutral verifier above and six requested focused failure families close that gap. After reviewing that correction and the complete verification evidence, ChatGPT Pro returned the exact verdict `CERA_ADULT_ON_EX_PROVIDER_FREE_ACCEPTED`. The verdict accepts only this provider-free implementation gate and grants no later authority.

## Still closed

- live Codex and DeepSeek story-role calls;
- adult story publication or production story data;
- production-world binding;
- SillyTavern and external-handler integration;
- route qualification/promotion;
- deployment.

This gate is closed. Any next gate must be selected and authorized separately by the creator.
