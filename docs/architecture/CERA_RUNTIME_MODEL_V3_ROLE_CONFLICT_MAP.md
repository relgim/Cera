# CERA Runtime Model V3 Role-Conflict Map

**Status:** controlling Stage 0 reconciliation record
**Program:** `cera-runtime-model-v3-alignment-001`
**Authority:** `docs/authority/CERA_RUNTIME_MODEL_V3.md`
**Scope:** D-186 continuous shadow route only; D-180 production behavior and frozen V7 evidence remain unchanged

## Controlling role matrix

| Stage | Sole owner | Authoritative output | Explicitly excluded |
|---|---|---|---|
| Ingress and authority assembly | deterministic Python | exact source units, claims, identities, evidence/cast/privacy boundaries | prose, psychology, semantic interpretation |
| Scene planning | Codex Planner | provisional causal plan after Python validation | visible prose, durable state, self-validation |
| Scene writing | stateless DeepSeek Writer | exact candidate prose only | semantic kinds, roles, claim certification, offsets, events, memory, acceptance |
| Mechanical envelope | deterministic Python | exact text hash, byte/character counts, paragraph ranges, candidate identity | prose semantics or repair |
| Semantic validation | independent Codex Validator | gap-free exact-span semantic ledger, presentation/material disposition, plus typed verdict | rewriting, repair, continuation, commit |
| Hard enforcement | deterministic Python | verified offsets/hashes/coverage, claim/cast/privacy/evidence enforcement, and exclusion of presentation-only spans from authority | heuristic semantic inference |
| Reader checkpoint | independent Reader | whole-response verdict, scores/reason codes, exact issue references | rewriting, hard-gate override, canon creation |
| Review-ready storage and commit | deterministic Python plus creator gate | immutable candidate receipts and creator-authorized atomic promotion | model-owned persistence or publication |

This table is the one controlling ownership matrix for the aligned shadow route. Other active documents must point here or reproduce the same ownership without adding a second owner.

## Conflict inventory and disposition

| Surface | Conflicting V7 assignment | Runtime V3 correction | Disposition |
|---|---|---|---|
| `src/cera/continuous/provider.py` | `ContinuousDeepSeekWireDraftV1` requires semantic segments, assertion kinds, owners, non-owning roles, protected claim keys, and realization certification. | Add `cera.scene_writer_draft.v1` containing only exact prose. Retain V7 DTOs and schema decoding solely for frozen historical evidence. | Correct in Stages 1-2. |
| `src/cera/continuous/provider.py` | `DeepSeekContinuousComposerPort` tells the Writer to classify and certify its own prose. | Writer prompt requests complete presentation-neutral prose only. Semantic fields are impossible in the active Writer schema. | Correct in Stage 1. |
| `src/cera/continuous/prompting.py` | Composer prompt asks for exhaustive semantic segments and protected-user realization entries. Validator prompt compares against Writer ledgers. | Writer prompt contains creative scope and hard constraints but no self-audit. Validator receives exact Writer bytes, mechanical envelope, plan, source authority, and cast, and creates the ledger independently. | Correct in Stages 1-2. |
| `src/cera/continuous/runtime.py` | Writer metadata is unpacked and can fail `validate_composer_realization()` before the stronger Validator sees the prose. | Every mechanically valid Writer draft reaches the Validator. Python freezes a mechanical envelope first; semantic enforcement occurs only after Validator output. | Correct in Stages 1-2. |
| `src/cera/continuous/evidence.py` | `_story_segments` are registered as Composer declarations; Python also uses text-name heuristics as semantic fallback. | Validate the complete Validator span ledger mechanically, then register only authority-bearing story/material spans; transient presentation remains visible but cannot enter accepted authority. | Correct in Stages 2-3. |
| `src/cera/continuous/contracts.py` | `StoryRealizationSegmentV1` and protected realization documentation describe Composer ownership. | Version active Validator-owned ledger contracts explicitly. Preserve old class readers as historical where needed. | Correct in Stage 2. |
| `src/cera/continuous/runtime.py` and sessions | No Reader gate exists; Planner and Validator are the only modeled reasoning sessions. | Add a provider-neutral, non-rewriting Reader result and prove Planner, Validator, and Reader physical-session separation. Reader is candidate-specific by default. | Correct in Stage 3. |
| Candidate/debug/receipt hashes | Candidate authority binds Writer semantic ledgers but no Reader verdict. | Bind Writer bytes/mechanical envelope, Validator semantic ledger, and Reader verdict separately. | Correct in Stages 2-3. |
| Tests and scripted Job 4 | Fixtures teach DeepSeek to self-classify and prove only internal consistency of that allocation. | Preserve frozen V7 evidence; add prose-only boundary tests, seeded semantic fixtures, non-rewrite/session-separation tests, and a complete fake V3 pipeline. | Correct in Stages 1-3. |
| `CERA_OWNER_ARCHITECTURE.md` | Composer output still includes source-coverage and beat/participant anchors. | Point active shadow architecture to the controlling matrix; Writer returns only prose. | Correct in documentation reconciliation. |
| `RUNTIME_PIPELINE_AND_PORTS.md` | D-186 active wording makes DeepSeek own exhaustive story segments before verification and omits Reader. | Mark V7 allocation historical/rejected for promotion and document Writer -> mechanical envelope -> Validator -> enforcement -> Reader. | Correct in documentation reconciliation. |
| `PROMPT_CONTEXT_AND_EXAMPLES.md` | Active context contract requires DeepSeek semantic anchors and role declarations. | Retain craft/context selection while removing semantic output duties from Writer. | Correct in documentation reconciliation. |
| schema catalog, state machine, roadmap, handoff, start page | Active entries encode or imply Composer semantic authority and lack the Reader checkpoint. | Add versioned V3 schemas/states, D-204 supersession, Stage 0-3 roadmap, and current handoff links without rewriting history. | Correct in documentation reconciliation. |

## Preserved work

- Persistent branch-bound Planner sessions, accepted-ancestry reconstruction, bounded evidence retrieval, branch/privacy custody, transactionality, restart recovery, and call accounting remain valid.
- DeepSeek remains the stateless prose Writer; its observed V7 semantic-contract failures do not disqualify its prose role.
- The independent Validator remains non-rewriting and gains sole semantic-ledger ownership.
- Python remains the only durable authority and commit owner.
- Frozen V7 provider outputs, reports, receipts, schemas, and historical readers remain byte-preserved and explicitly non-promotable.
- The deferred consensual-adult capability fallback remains documentation-only under Queue 0044.

## Stage 0 exit decision

The root defect is an owner-allocation conflict: a prose Writer was required to perform and certify semantic analysis before independent validation. The smallest coherent correction is to move semantic span ownership entirely to the Validator, keep Python mechanical, and insert an independent Reader gate. No provider call or production-route change is needed to prove that boundary.
