# Phase 15 Provider-Free Live-Shaped Turn Pipeline Result

**Date:** 2026-07-28  
**Status:** accepted by ChatGPT Pro within the provider-free live-shaped boundary  
**Authorization:** D-068  
**Scope:** Python-owned Composer context assembly and typed Codex-to-DeepSeek orchestration using real Hanezawa evidence and fake provider transports only

## Outcome

CERA now has a non-committing `LiveShapedTurnPipeline` that runs the actual provider-specific role adapters through the established Python authority layers without making a network/provider call.

The tested order is:

```text
validated SceneReasonerRequest
-> CodexSceneReasonerPort with fake SDK output and real request-bound evidence dispatcher
-> ReasonerCoordinator validation
-> transient retained exact evidence
-> ComposerContextAssembler
-> SceneComposerRequest with selected realization cards
-> DeepSeekSceneComposerPort with fake HTTP output
-> ComposerCoordinator validation
-> in-memory presentation-neutral AcceptedStoryArtifact
-> LiveShapedTurnReceipt
```

There is no commit step in this milestone. A successful result still reports `story_state_committed: false` and zero story-authority writes.

## Exact-evidence handoff

Before Phase 15, the Reasoner coordinator retained metadata proving that evidence had been fetched but discarded the corresponding exact sections after validation. That would force the Composer path either to refetch decision evidence blindly or to lose the evidence Codex actually used.

`ReasonerExecutionResult` now retains the authorized exact seed/fetched records transiently. This material is not added to `SceneReasonerReceipt`, provider receipts, the MCP bridge receipt, telemetry, or durable story state. The Composer context assembler selects from it only when the validated move, participation choice, or current beat actually used that evidence.

`EvidenceMetadata` now carries the record's bounded tags so Python can classify Genesis relationship, memory, development, and speech records without opening filesystem artifacts or treating free text as a type discriminator.

## Composer context assembly

`ComposerContextAssembler` creates `cera.composer_realization_context.v1` after participant selection. It:

1. reuses exact decision evidence already authorized and retained by the Reasoner;
2. computes which selected character may receive each cited record from validated move/participation/beat use;
3. performs a deterministic tag-bound `speech_system` search for each selected NPC;
4. requires exactly one unambiguous authorized voice record per selected NPC;
5. exact-fetches only that record's `source_text` section;
6. adds the voice record as a versioned Composer continuity reference;
7. labels creator craft separately and requires it to match the adult route binding;
8. applies the existing Genesis, branch, privacy, knowledge-owner, selected-character, route, content-class, uniqueness, and byte-budget checks;
9. emits `cera.composer_context_assembly_receipt.v1` with evidence/craft IDs, lookup receipt IDs, hashes, byte counts, zero provider calls, and zero authority writes.

The assembler uses a fresh local EvidenceService budget over the same immutable snapshot. It does not give DeepSeek retrieval access. Inactive-character cards, uncited facts, private memories belonging to another character, unselected craft, and arbitrary repository content do not enter the packet.

If a speech record was itself Reasoner-cited, Python replaces the smaller cited block with the exact voice expansion rather than duplicating its evidence ID.

## Turn orchestration and receipt

`ComposerRequestPlan` supplies the already authorized exact source packet, scene/profile settings, continuity references, event-coverage requirement, hard boundaries, adult binding, and selected craft blocks. It does not decide cast or psychology.

`LiveShapedTurnPipeline`:

- stops before context/Composer work when the Reasoner result is not `decision_ready`;
- constructs the Composer request from the validated Reasoner result;
- assembles context locally;
- invokes the typed Composer once;
- returns a transient result and `cera.live_shaped_turn_receipt.v1`;
- performs no persistence, rendering, consolidation, retry, or fallback.

The top-level receipt binds request/branch/generation/snapshot, Reasoner provider receipt and normalized receipt hash, context assembly receipt and hash, Composer provider receipt and normalized receipt hash, accepted artifact and hash, all lookup receipt IDs, provider-call count, and explicit zero-write/uncommitted state. `LiveShapedTurnResult` recomputes every component hash and call count.

Under the fake transports, the two live-shaped adapter receipts each report one simulated external provider call, so the top-level count is two. No real provider/network request occurred.

## Real-Genesis workflow evidence

The new tests use an auto-deleting SQLite database with the installed Hanezawa Core Genesis V1.1 package.

### Ordinary multi-character

Hana and Mia are selected by a validated decision. Their exact relationship evidence and only their two speech systems reach the Composer. Both typed adapters run once. The candidate reaches in-memory acceptance, while source/artifact/authority tables remain unchanged. Reopening the database still shows zero story artifacts.

### Indirect owner memory

The Reasoner performs a bounded `missed` + `calls` search and exact expansion of Hana memory `H-M08`. The retained exact memory becomes a Hana-only `CURRENT_STATE` block, including the learned pressure around maintaining faith. It is not applicable to Mia or any other character. Hana's voice card is added separately.

### Consent-valid protected route

A synthetic exact marker is present in the protected Composer envelope and absent from the Codex prompt. Runtime Codex receives only the synchronized non-graphic ledger. DeepSeek's packet receives the exact protected envelope plus only the current synthetic craft references selected by the adult binding. The complete path remains uncommitted with zero story writes.

No Adult EX text was imported or used.

### Failures and restart

- `insufficient_evidence` stops after one fake Codex-shaped call; context assembly and DeepSeek do not run.
- A simulated DeepSeek outage occurs once, produces the existing explicit Composer failure, and triggers no retry/fallback or database change.
- Successful in-memory composition survives a store reopen as no durable story change, because no commit bundle was created.

Existing suites continue to cover blockers, immutable sibling regeneration, fork cutoff isolation, crash rollback, exact replay, and restart recovery at their authority-owning layers.

## Validation

```text
python -m unittest tests.test_live_shaped_pipeline -v
Ran 6 tests
OK

python -m unittest discover -s tests -p "test_*.py"
Ran 231 tests
OK (skipped=1 optional live probe)
```

`compileall` completed cleanly. Documentation validation is run again after recording this result.

## Deliberate non-claims

Phase 15 does not prove:

- live Codex or DeepSeek behavior;
- reasoner judgment, query quality, prose quality, voice fidelity, buildup, or multi-scene quality;
- full-Genesis context-selection recall or optimal packet compression;
- adult prose or Adult Mechanics provider quality;
- automatic candidate-event extraction, durable commit, memory consolidation, or rendering;
- SillyTavern, production-world binding, route promotion, deployment, or creator acceptance.

It also does not claim that transient exact evidence is safe to log. It is runtime-only data and must remain outside durable operational receipts and telemetry.

## Next gate

After Pro review, the next safe technical milestone is transactional ordinary-turn integration: create a typed commit-bundle builder that accepts only the validated presentation-neutral artifact and matched receipts, commits source/artifact/provider-validation evidence atomically, and leaves derived memory to the already separate deferred consolidator. Tests must cover exact replay, regeneration siblings, forks, rollback, and restart using fake transports before any production or SillyTavern work.

Live story/Genesis/adult provider calls, Adult EX import, production binding, SillyTavern, route promotion, deployment, and final creator acceptance remain separately closed.

## ChatGPT Pro review

ChatGPT Pro returned exactly `PHASE_15_LIVE_SHAPED_TURN_PIPELINE_ACCEPTED` with no in-scope correction. The verdict accepts transient exact-evidence reuse, selected-character voice-card assembly, same-snapshot/separate-budget retrieval, non-authoritative context/turn receipts, real-Genesis fake-transport workflow coverage, craft/fact and safe/exact separation, and explicit logging/non-claim boundaries. It does not qualify live providers or authorize content calls, durable publication, production binding, SillyTavern, promotion, or deployment.
