# CERA Runtime Model V3

**Status:** controlling manager architecture decision  
**Version:** `cera.runtime_model.v3`  
**Date:** 2026-08-03  
**Owner:** Ted  
**Manager:** ChatGPT Pro repository-review thread
**Deferred consensual-adult capability fallback:** `CERA_CONSENSUAL_ADULT_CAPABILITY_FALLBACK_V1.md`

## 1. Decision

CERA will stop treating provider-contract failures as isolated schema defects and will restore one closed division of responsibility:

```text
Ted / creator source
-> deterministic Python authority kernel
-> Codex Scene Planner
-> deterministic Python plan compiler
-> DeepSeek Scene Writer
-> deterministic Python mechanical envelope
-> independent Codex Semantic Validator
-> independent Reader checkpoint
-> deterministic Python acceptance and commit
-> creator review / rendering
```

The Writer writes. The Validator interprets what the writing actually asserts. The Reader judges the complete reader-facing result. Python owns exact authority, mechanical custody, and transactions. No model certifies its own semantic correctness.

This document supersedes conflicting role-allocation language in the D-186 continuous shadow implementation and its later correction cycles. It does not rewrite historical evidence, consumed decisions, accepted story state, or the active D-180 production profile. The useful persistent-session, evidence-custody, branch, transaction, and recovery work remains valid.

## 2. Why the documented lesson was repeated

The existing `CERA_OWNER_ARCHITECTURE.md` already gave DeepSeek a minimal prose-composition role and explicitly denied it semantic-inference records, participant manifests, consent state, durable truth, and Python-owned bookkeeping.

The later D-186 route drifted away from that boundary. Successive corrections required DeepSeek to return exact protected-user realization spans, exhaustive semantic segment ledgers, ownership classifications, mutually exclusive role sets, and claim keys. The iterative review workflow repaired each immediate validation failure inside that local design instead of asking whether the task belonged to DeepSeek at all.

The result was the same failure pattern seen in Vera:

1. a non-thinking prose model was assigned semantic reasoning and self-audit;
2. Python interpreted those declarations literally;
3. ambiguous output failed before the stronger Codex Validator could inspect it;
4. each failure added more schema and prompting;
5. local tests proved contract consistency but not correct role ownership.

The 2026-08-03 frozen repeatability campaign makes the issue measurable: DeepSeek Flash non-thinking completed `20/20` identical requests but only `4/20` passed the V7 semantic Composer contract. The failure is architectural, not a reason to reject DeepSeek as a prose writer.

## 3. Non-negotiable ownership

### Ted / creator

Ted owns:

- his supplied source message;
- his unsupplied dialogue, action, movement, thought, emotion, consent, decision, commitment, and next consequential choice;
- product goals and final creator-facing acceptance decisions.

### Deterministic Python authority kernel

Python owns:

- exact ingress bytes, typed source units, protected-user claims, IDs, hashes, offsets, branch and generation identity;
- evidence access, visibility, knowledge ownership, active-record versions, and cast eligibility;
- provider orchestration, call accounting, timeouts, no-fallback enforcement, and immutable evidence;
- exact text hashing, UTF-8 validation, length ceilings, paragraph boundaries, and gap/overlap checks;
- validation of Validator-provided spans against the exact Writer bytes;
- exact protected-user source matching and hard authority enforcement;
- candidate identity, atomic commit, rollback, restart, branch, memory, event, and rendering custody.

Python does not infer psychology, interpret ambiguous natural-language ownership, or write story prose.

### Codex Scene Planner

The Planner owns:

- character perception, motive, conflict, tactic, choice, and causal consequences;
- active NPC cast and conversational-floor selection;
- current-turn causal beats and optional conditional future direction;
- evidence needs, uncertainties, prohibited inferences, protected-user stopping boundary, and open realization space;
- the exact point at which the response must stop before Ted's next unsupplied choice.

The Planner does not write the visible scene. Its output is advisory until Python compiles and validates it.

### DeepSeek Scene Writer

The Writer owns only:

- visible story prose;
- dialogue wording, gesture, staging, pacing, atmosphere, imagery, rhythm, and allowed interiority;
- complete realization of the validated plan in natural prose.

The default candidate adapter remains DeepSeek Flash non-thinking because its purpose is writing rather than analysis. DeepSeek is stateless from CERA's perspective. Python sends only the current validated plan, authorized character/voice material, current public scene anchor, allowed private material, current creator source, and an optional bounded tail of accepted visible prose.

The Writer must not return or decide:

- semantic assertion kinds;
- owner, actor, speaker, subject, affected, addressed, observing, or referenced role ledgers;
- protected-user claim keys or realization certifications;
- consent/capacity truth;
- active-cast authority;
- event, memory, relationship, persistence, or durable-state records;
- offsets, hashes, IDs, coverage booleans, or acceptance decisions;
- whether its own prose is safe or plan-faithful.

The target Writer wire is intentionally minimal:

```json
{
  "schema_version": "cera.scene_writer_draft.v1",
  "story_text": "<exact candidate prose>"
}
```

Python may later accept raw text instead of this wrapper if the adapter contract is simpler and equally auditable. No semantic field may be added to the Writer wire without a new creator-reviewed architecture decision.

### Codex Semantic Validator

The Validator receives the exact Writer bytes plus the validated plan, exact source authority, active cast, evidence bindings, and accepted scene anchor. It independently determines what the prose actually says to a reader.

It owns:

- gap-free semantic span classification over the exact candidate text;
- action, dialogue, private-state, public-state, and neutral-environment assertion ownership;
- non-owning relations such as affected, addressed, observing, and referenced;
- protected-user assertion detection and exact-source comparison candidates;
- plan/beat realization, cast, evidence, privacy, continuity, and stopping-boundary judgment;
- explicit accept, reject, or inconclusive status with typed reason codes.

The Validator cannot rewrite, repair, continue, summarize away, or reinterpret the prose into a safer candidate. Ambiguity is a rejection, not permission to guess.

Python verifies every Validator offset, exact text hash, role identity, claim reference, and coverage statement before using the verdict.

Accepted and concern decisions use the strict canonical realization segment
and protected-adjudication family. Rejected, inconclusive, and error decisions
use a separate diagnostic-only family. That family may represent an ungrounded
Ted assertion with zero claims only through an explicit typed violation, has
Python-derived exact text/hash custody, and cannot enter finalization or canon.

### Independent Reader checkpoint

The Reader is a separate provider-neutral role and receives the exact candidate, validated plan goals, bounded accepted context, and hard constraints. It performs final whole-response review for:

- scene completeness and causal development;
- character voice and behavioral realism;
- dialogue naturalness, pacing, repetition, and readability;
- premature scene closure, skipped buildup, protagonist worship, inactive-character intrusion, or implausible reactions;
- reader-visible protected-user or continuity problems missed by local span classification;
- whether the response is worth presenting to the creator.

The Reader returns only a verdict, scores/reason codes, and exact issue references. Every reason code and issue code uses the same closed lower-snake local-key surface enforced by both the submitted provider schema and Python; output is never normalized after generation. It cannot rewrite or repair prose. Hard Validator/Python failure cannot be overridden by the Reader. Reader approval cannot create canon.

### Python acceptance and creator review

Only after mechanical validation, Semantic Validator acceptance, Reader acceptance, and all deterministic authority checks may Python create a review-ready immutable candidate. Creator acceptance then controls promotion where the active product profile requires it. Only Python commits story truth.

## 4. Canonical runtime flow

1. **Ingress:** Python freezes the exact creator message and typed source units.
2. **Authority assembly:** Python assembles branch truth, evidence, cast eligibility, privacy, and protected-user boundaries.
3. **Planning:** one branch-bound Codex Planner turn produces a causal plan.
4. **Plan compilation:** Python validates and compiles the plan; invalid plans never reach the Writer.
5. **Writing:** one stateless DeepSeek Writer call produces only candidate prose.
6. **Mechanical envelope:** Python freezes the exact text and derives hashes, byte counts, paragraph ranges, and candidate identity. It performs no semantic prose inference.
7. **Semantic validation:** an independent Codex Validator classifies exact spans and judges plan/authority compliance.
8. **Deterministic enforcement:** Python verifies the span ledger, protected-user source matches, cast, privacy, evidence, and complete coverage.
9. **Reader review:** an independent Reader evaluates the complete visible response without editing it.
10. **Review-ready candidate:** Python stores an immutable presentation-neutral candidate and complete receipts.
11. **Creator action:** Accept, Decline, Adjust, or governed False Positive behavior follows the active creator-review contract.
12. **Commit:** Python atomically promotes accepted story/event/memory work and then renders it.

## 5. Session and memory model

- **Planner:** persistent branch-bound Codex thread containing only accepted ancestry plus current authority deltas. Rejected candidates never enter accepted lineage.
- **Validator:** physically separate branch-bound Codex role/session. It never inherits Planner conversation or treats provider history as authority.
- **Reader:** fresh candidate-specific session by default to preserve independence. It receives bounded accepted context explicitly.
- **DeepSeek Writer:** stateless request. A stable prefix or provider cache may improve cost/latency, but neither is memory or authority.
- **Python/SQLite:** the only durable story, branch, memory, relationship, event, and acceptance authority. Every provider session is disposable and reconstructable.

## 6. Failure and bounded recall policy

- No silent provider substitution.
- Codex, Validator, and Reader retain no automatic retry under the same candidate/run identity.
- DeepSeek may use at most three total fresh attempts per stage under one frozen authority package, with immutable attempt identities, no output merging, and exact call accounting. The detailed deferred rule is in `CERA_CONSENSUAL_ADULT_CAPABILITY_FALLBACK_V1.md`.
- Python deterministic failures are never masked by DeepSeek recall; they stop for diagnosis.
- No Python semantic repair of prose.
- No Validator or Reader rewriting.
- A mechanically readable Writer candidate reaches the Validator even when Writer-supplied hints are absent or wrong, because Writer hints are non-authoritative.
- A failed candidate is preserved as immutable evidence and remains outside accepted ancestry.
- A later regeneration is a fresh candidate identity, not an in-place retry.
- Any future repair stage requires separate qualification and cannot combine generation with independent judgment.

## 6A. Deferred consent-valid capability-restricted route

When Python has already established adult identity, present consent, capacity, and freedom to stop, but the selected Codex Planner adapter returns a typed capability restriction, CERA may later use the separately governed route in `CERA_CONSENSUAL_ADULT_CAPABILITY_FALLBACK_V1.md`.

Codex freezes sequence and character material through the last supported point and creates a safe DeepSeek handoff prompt. Python combines that package with exact creator source for a DeepSeek Planner, Writer, and Safe-Continuity Formatter. Only the Formatter's retained non-graphic package returns to Codex and ordinary Memory / Directory authority. The raw restricted-interval prose is never represented as Codex-validated semantic truth.

One typed restriction opens a bounded restricted interval; CERA does not repeatedly call Codex for the same refusal. This route is deferred and receives zero implementation or live-call authority under Queue 0044.

## 7. Executable architecture invariants

The implementation must have tests that fail when any of these conditions is violated:

1. The Writer DTO contains ownership, protected-claim, role-ledger, consent, event, memory, persistence, semantic-kind, offset, hash, or acceptance fields.
2. Python uses prose heuristics or grammar rules to decide semantic ownership.
3. Writer metadata can block a mechanically valid candidate from reaching the Validator.
4. The Validator can alter story bytes or return replacement prose.
5. The Reader can alter story bytes or return replacement prose.
6. Planner, Validator, and Reader share a physical provider thread.
7. DeepSeek prior conversation is treated as story memory or authority.
8. A rejected candidate enters accepted Planner ancestry, world state, event state, memory, or visible publication.
9. Any model directly commits story truth.
10. Documentation assigns the same semantic responsibility to more than one role or contradicts this ownership matrix.
11. A DeepSeek stage exceeds three total attempts, merges outputs across attempts, or mutates its frozen stage input between attempts.
12. A Python deterministic defect is treated as a reason to recall DeepSeek.
13. Raw capability-restricted prose is sent to Codex or used directly as Memory / Directory authority instead of crossing through the safe retained package.
14. Rejected diagnostic spans or adjudications appear in an accepted/concern decision, finalization package, Reader request, review-ready candidate, event, memory, persistence operation, accepted ancestry, or commit.
15. The active Reader provider schema permits a reason code or issue code that the Python Reader DTO rejects, or transport output is normalized to bridge such a mismatch.
16. A positive Reader qualification fixture expects acceptance while its prose asserts protected-user action, dialogue, state, or choice absent from that fixture's explicit accepted context and source claims.
17. A positive Reader qualification fixture's prose uses a character-owned semantic channel absent from its explicit ordered Planner role beats.
18. A qualification harness broadens the production thread-lineage purpose contract or attaches obsolete campaign labels instead of the active `primary_planner` and `primary_validator` purposes.
19. A qualification harness supplies filesystem-backed scene or turn identifiers outside the active bounded slug contract, or weakens production validation to accept historical campaign punctuation.
20. The active Planner provider schema permits any output local-key field that the closed Python Planner DTO rejects, or provider output is normalized after generation to hide such a mismatch.
21. The active Validator prompt leaves diagnostic protected relations ambiguous against their exact role arrays, or permits `npc_assertion_owner_ids` to differ from the span's complete non-Ted assertion-owner set.
22. The active Validator prompt permits canonical or diagnostic span offsets outside the exact zero-based Python Unicode-codepoint range, permits gaps or overlaps, or permits the final end offset to differ from the immutable Writer text's codepoint count.
23. The active Validator prompt permits a canonical or diagnostic span's semantic kind to disagree with its exact assertion-owner roles, including state-owned narration, or leaves mixed semantic kinds unsplit.

A documentation test must identify one controlling role matrix and reject conflicting active claims in `CERA_OWNER_ARCHITECTURE.md`, `RUNTIME_PIPELINE_AND_PORTS.md`, the schema catalog, decision ledger, roadmap, and handoff.

## 8. Roadmap

### Stage 0 — Architecture reconciliation, provider-free

- inventory every active document, schema, prompt, DTO, validator, and test that assigns semantic bookkeeping to DeepSeek;
- mark the V7 semantic Composer contract as rejected for promotion while preserving all evidence;
- reconcile controlling documentation to this model;
- record explicit supersession rather than silently editing history.

Exit: one conflict map, one role matrix, and zero contradictory active ownership statements.

### Stage 1 — Minimal Writer contract, provider-free

- introduce the minimal Writer draft containing exact prose only;
- remove Composer-owned semantic roles, protected claim keys, offsets, realization booleans, events, and persistence data from the active candidate path;
- keep Python mechanical text custody and transport accounting;
- preserve historical schema readers only for immutable evidence.

Exit: Writer schema-boundary tests prove no semantic fields can re-enter.

### Stage 2 — Validator-owned semantic ledger, provider-free

- define a gap-free exact-span Validator output;
- make Codex, not DeepSeek, classify assertion ownership and protected-user implications;
- make Python verify exact offsets/text/hashes and enforce source authority;
- reject ambiguity without repair.

Exit: seeded good/bad prose fixtures produce exact deterministic acceptance or rejection, including mixed clauses such as `Hana smiled as Ted stepped inside.`

### Stage 3 — Reader checkpoint and complete fake pipeline, provider-free

- add a provider-neutral Reader port and non-rewriting verdict schema;
- run the complete Planner -> Writer -> Validator -> Reader -> review-ready path with scripted transports;
- prove restart, regeneration, branch, rejection, and no-mutation behavior;
- run one complete repository suite on the frozen candidate.

Exit: all role-boundary, authority, transaction, and documentation gates pass.

### Stage 4 — Small live qualification, separately manager-authorized

Only after Stages 0-3 pass:

- qualify the minimal DeepSeek Writer wire for mechanical reliability;
- qualify Codex semantic validation on exact writer bytes and seeded violations;
- qualify Reader judgment separately;
- then run a small set of full end-to-end scenes with no retry or repair.

The first live set should be small and varied, not another long repeated campaign. It must include ordinary dialogue, multi-character participation, a tempting protected-user invention, private-state ownership, and a scene that must remain open.

### Stage 5 — Two-turn continuity and latency

- use one accepted Planner lineage across two turns;
- keep DeepSeek stateless;
- prove accepted-reference reconstruction and lean continuation without full-history replay;
- measure first reasoning, first structured output, completion, token accumulation, cache, and response quality separately.

### Stage 6 — Isolated SillyTavern qualification

Run two consecutive isolated real routes with copied databases, loopback-only services, no production acceptance, and complete creator-review artifacts.

### Stage 7 — Twenty-turn campaign

Run one immutable bounded campaign at a time. Measure sustained character logic, cast discipline, memory correctness, prose quality, protected-user autonomy, latency, failure rate, and recovery.

### Stage 8 — Promotion decision

Promotion requires:

- zero hard authority defects;
- high Writer mechanical reliability;
- Validator detection of every seeded hard violation;
- Reader and blinded human quality acceptance;
- stable branch/restart/regeneration behavior;
- explicit creator approval of the final profile.

### Stage 9 — Deferred consensual-adult capability fallback

After the normal Runtime Model V3 route is provider-free complete and separately qualified, a newer manager queue may implement the consent-valid Codex-capability fallback defined in `CERA_CONSENSUAL_ADULT_CAPABILITY_FALLBACK_V1.md`. It is not part of Stages 0-3 and cannot be inferred from their completion.

## 9. Current effect

Further live provider experiments are paused while Stages 0-3 are reconciled. The Queue 0043 creator budgets remain available but frozen at the last verified balance:

- Sol/Codex-family remaining: `491`;
- DeepSeek-family remaining: `459`.

No sequence 30 is allocated by this decision. No active production route, story database, installed SillyTavern profile, service, deployment, merge, remote, or push authority changes.
