# Behavioral Consolidation and Human-Test Gate Result

**Decision:** D-165  
**Date:** 2026-07-30  
**Status:** ready for governed creator testing of the ordinary/relationship route

## Outcome

CERA now runs the intended development path:

```text
raw SillyTavern cue and creator controls
-> Python authority classification, seed dossier, and bounded evidence service
-> Sol-medium Scene Reasoner
-> Python reasoner validation and selected Composer context
-> DeepSeek V4 Flash thinking Composer
-> Python structural validation and provisional display
-> independent Sol-medium realization/creator review
-> creator action
-> zero-provider atomic Accept and durable publication
```

Python remains the authority for identity, privacy, knowledge, branch state,
validation, publication, and durable memory. Sol owns evidence-backed causal
reasoning and independent review. DeepSeek owns one complete prose candidate.
The creator decides whether the candidate becomes canon. There is no automatic
retry, fallback provider, Detailer, or provider call during Accept.

## Implemented corrections

- Reasoner adapter/prompt v18 and packet v13 expose a bounded four-search
  retrieval contract. Compact search results are not exact evidence; exact
  section expansion is free of the search-operation limit and remains byte
  bounded. Tool-budget state is published to runtime Codex.
- Composer adapter v24, packet v15, prompt v22, and the runtime-v2 modular
  prompt registry use DeepSeek V4 Flash with thinking enabled. Only selected
  participants, relevant evidence, active voice material, creator controls,
  and the validated broad causal plan are supplied.
- Sequence entries are broad causal event blocks. Appraisal, motive, tactic,
  authority, transition purpose, and consequence are writer scaffolding, not
  extra paragraphs or event quotas.
- Depth supports Short, Auto, Medium, Long, and Epic without numeric beat or
  word quotas. Autonomy supports Off, Mind, Body, and Both. Prompt handling
  supports Adjustment and Modification.
- Relationship and character change is atomic: occurrence, notice,
  interpretation, emotion, attraction, attachment, and durable development do
  not silently imply one another.
- SillyTavern displays provisional prose immediately, then attaches the Codex
  sequence plan, Sol assessment, and Accept/Correction/DeepSeek/Codex/Decline
  actions when review completes. Review survives reload. Provisional material
  is excluded from export and durable story authority.
- Accept commits only the reviewed presentation-neutral prose and prepared
  package, in one local transaction, with zero provider calls and zero retry.
  Corrections produce a typed diagnostic classified as user, Reasoner,
  Composer, Python, or missing-authoring-context work.

## Verification

### Live ten-turn qualification

Evidence:
`evaluation/evidence/continuous_ten_turn_qualification_2026-07-30_v27_flash_thinking`

- 10/10 accepted cases on fresh immutable evidence identities.
- 10 Sol-medium Reasoner calls, 10 DeepSeek V4 Flash thinking Composer calls,
  and 10 Sol-medium verifier calls.
- One attempt per case and stage; no retry or fallback.
- Direct and indirect memory, paraphrased lookup, multi-character context,
  restart, regeneration, fork isolation, and accepted-state continuity passed.
- Required indirect evidence was retrieved and cited through bounded tools.
- SQLite integrity was `ok`; foreign-key findings were empty.

### SillyTavern UI smoke

The first real UI attempt exposed a shared lifecycle bug: response metadata was
available before the assistant message DOM existed, so the creator-review panel
could not attach. The bridge now queues only bounded provisional CERA metadata,
stores it at message receipt, renders after the character message exists, and
recovers on reload.

A provider-free UI probe then passed provisional display, background review,
reload recovery, all creator buttons, Accept, and panel removal. A final real
UI smoke displayed DeepSeek prose with the Codex sequence and Sol assessment.
Clicking Accept committed one artifact, advanced the disposable branch from
generation 0 to 1, emitted a zero-provider/zero-retry acceptance receipt, and
left database integrity clean. The disposable smoke database and its rejected
diagnostic attempts remain preserved under
`runtime/development/smoke/20260730-post-display-v1`.

### Provider-free suite

- 497/497 tests passed in 245.343 seconds.
- Coverage includes contract/schema differential checks, malformed output,
  privacy, branch, supersession, restart, retrieval, creator review, acceptance
  timing, source inventory, installed SillyTavern bridge equivalence, and
  provisional-export exclusion.
- The normal non-production human-test service was restored on loopback port
  5101 after smoke verification.

## Qualified claim and limitations

CERA is ready for creator testing of ordinary and relationship scenes. This is
not production readiness, a promoted route, or proof of universal prose
quality. Live Adult ON/EX publication, a production world, public deployment,
and an external handler remain closed.

End-to-end latency remains roughly two to three minutes or more because the
route uses three sequential provider stages. The qualification establishes
mechanics, authority, retrieval, isolation, and fail-closed behavior; ongoing
creator testing must still evaluate scene length, character subtlety, voice,
and writing quality. Reversible present texture is intentionally provisional
until accepted by the creator.

## ChatGPT Pro advisory review and Codex decision

Pro found no blocking ownership or prompt conflict, judged the ordinary-route
handoff coherent, and agreed that the evidence supports sustained local creator
testing rather than production, Adult activation, or a universal writing claim.
It highlighted four non-blocking boundaries: compact search must not replace
exact evidence; autonomy controls must not grant Ted invention; Adjustment and
Modification must enter before the validated plan; and accepted prose must not
turn every reversible embellishment into objective state.

Codex independently checked those points against the implementation and accepts
them as accurate cautions, not unresolved corrections:

- compact hits are references only and hard decisions require authorized exact
  fetch; fetch is free of the four-search-operation count, not of section,
  snapshot, privacy, supersession, or byte limits;
- Mind/Body/Both protects NPC mental and involuntary body claims and supplies no
  authority for Ted thought, dialogue, consent, or consequential action;
- prompt-handling mode is typed creator input classified by the Reasoner before
  Python validates the SequencePlan;
- the accepted prose artifact supplies continuity, while typed direct events
  and later validated consolidation determine factual and durable state.

Pro's highest-value next improvement is a structured creator-labeled quality
corpus from sustained manual tests, recording controls, plan, selected context,
candidate, Sol assessment, creator action/reason, eventual accepted result, and
per-stage latency. Codex agrees. The existing review records, action receipts,
diagnostics, and telemetry are the correct source evidence; a later reporting
gate should assemble approximately 20-30 varied resolved creator turns before
adding more broad prompt rules.

## Next governed action

Use `Hanezawa Family - Cera v1.0` in SillyTavern for ordinary/relationship
human tests. Exercise Accept, Correction/Adjustment, DeepSeek rewrite, Codex
replan, Decline, regeneration, and a new-chat branch. Preserve failures as
diagnostic evidence. Adult live publication and latency optimization each
require a later explicit gate.
