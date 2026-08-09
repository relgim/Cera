# CERA Handoff for the Vera v2 Logic and Rational-Processing Review

## Status and authority

This is a review and design task only.

Do not modify CERA or Vera source, dispatch provider calls, change an active
route, mutate story state, modify SillyTavern, or publish manager authority.

The reviewer is the Codex operator from the `Vera v2_part1` task who worked on
the original Vera system. Use that experience to identify what CERA should
preserve, replace, or redesign in its reasoning and data architecture. Do not
assume Vera is automatically correct and do not port its implementation
unchanged.

Vera is reference evidence. CERA is the product being designed.

## Mission

Give CERA an owner-level recommendation for:

- how character logic should be processed;
- how a character decision should be rationalized;
- how competing motives, pressures, memories, relationships, knowledge, bodily
  reactions, and user direction should influence a choice;
- what reasoning should be retained in a structured decision record;
- what should remain model-private and disposable;
- how current character data, events, memories, and relationships should be
  stored and retrieved;
- how accepted decisions become durable branch state;
- how alternate outcomes, Regenerate, Repair, and Replan should work;
- what important logical or data-processing responsibility Ted and the current
  CERA review may have missed.

The goal is not to reproduce Vera's middleware. The goal is to recover any
valuable cognition principles from Vera and design the best achievable CERA
architecture around the current Codex, DeepSeek, Pi, Python, SillyTavern, and
branch-state constraints.

## Current CERA checkpoint

```text
Repository:
D:\Cera\worktrees\C78-quality-fixes

Branch:
fix/cera-runtime-quality-20260809

Runtime source checkpoint:
1cc8ff8cce3496b00bd1641d993b94427d6ea417

Runtime source tree:
10c2bc3dcee7e48e9f0abb394e87bdf6883de24f

Documentation-only descendant:
1b93b617dd4ab897b99b1c4da1c0f9bb3e472a96
```

The runtime checkpoint passed 128 focused provider-free tests with one Windows
symlink-privilege skip. That is not complete-suite or live SillyTavern
qualification.

The manager repository still points to Queue 0077 / Roadmap 0031, which freezes
the older `58ae752` lean route. That measurement authority is stale relative to
the current correction branch and the creator decisions below. Do not execute
it as part of this review.

## Required reading

Read completely, in this order:

1. `D:\Cera\worktrees\C78-quality-fixes\docs\review\CERA_RUNTIME_ROUTE_AND_FILTER_VALIDATOR_RECONCILIATION_REQUEST.md`
2. `C:\Users\Ted\Downloads\CERA_ARCHITECTURE_AND_ROADMAP_RECONCILIATION_REVIEW_2026-08-09.md`
3. `C:\Users\Ted\Downloads\CERA_DECISION_RATIONALE_ROADMAP_2026-08-09.md`
4. `D:\Cera\worktrees\C78-quality-fixes\docs\handoff\CREATOR_DECISIONS_AND_LESSONS.md`
5. The current code paths listed later in this handoff.
6. The exact Vera source and design records you previously used, after
   identifying their repository and version. Likely reference roots include:

   - `D:\AIChatBot\Vera_v2`
   - `D:\AIChatBot\Vera_v2_d3`

Do not treat either Vera directory as CERA authority. State which Vera version,
commit, files, or runtime behavior supports every comparison.

## Settled creator direction

### Logic ownership

One visible candidate has one logic owner.

```text
Ordinary turn:
Codex owns character logic, choices, psychology, causal sequence, consequences,
participant response, conversational floor, and stopping point.

Adult-route turn:
DeepSeek owns the complete adult logic and prose for that candidate.
```

A typed transition may select the owner before visible generation or select the
owner of the next prompt. One visible response must not change logic owner
halfway through.

### Ordinary realization and validation

```text
Codex Planner
-> Pi-hosted DeepSeek ordinary Writer
-> separate Luna Extra High semantic validation
-> Python deterministic validation
-> automatic Accept when every required check passes
-> ordinary Recorder for compatible secondary continuity
```

The core Validator interface should remain provider-neutral even if Luna Extra
High is the required initial adapter.

### Adult realization and filtered continuity

```text
Pi-hosted DeepSeek Adult Scene
-> separate Pi-hosted DeepSeek Adult Filter/Validator
-> staged protected full record
   + staged synchronized Codex-safe projection
   + staged next-route state
-> Python deterministic validation
-> automatic Accept when every required check passes
-> atomic promotion
```

The separate Adult Filter replaces the post-Accept Adult Recorder unless a
truly non-duplicated responsibility is found.

The protected full record remains available only to authorized DeepSeek adult
roles. Codex receives only the non-explicit projection needed to understand the
accepted consequences and resume ordinary reasoning.

Do not resolve or implement deferred adult eligibility policy in this review.

### Acceptance and provisional canon

- A candidate automatically becomes accepted canon only after its required
  semantic model check and Python deterministic check pass.
- A failed candidate never auto-accepts.
- Ted must be offered a separate manual `Accept as Provisional Canon` action for
  a failed candidate.
- A provisional record has a stable ID.
- Any later accepted scene that depends on it must carry that provisional ID
  and remain marked as provisionally dependent until the source is resolved or
  superseded.

### Regenerate, Repair, and Replan

These are separate actions:

```text
Regenerate:
same exact frozen logic/authority
no failure feedback by default
complete independent alternate realization

Repair:
same exact frozen logic/authority
one concise typed semantic conflict
complete replacement, never a sentence patch

Replan:
same exact user source
optional creator guidance
new Codex causal/psychological sequence
old candidate invalidated
```

The current creator choice is a maximum of two complete Writer candidates per
frozen authority before the final candidate and reason are shown to Ted.

One automatic Repair may be appropriate for a critical material conflict.
Noncritical or subjective concerns should be shown to Ted rather than silently
steering another sample.

### Character autonomy

The existing global control is:

```text
off | mind | body | both
```

The initial default is `both`. Character mind and body therefore have greater
precedence than a user-declared NPC outcome. The user may change the button at
will.

Interpretation supplied by Ted:

- `mind`: the character's reasoning, personality, judgment, and decision can
  override the user's declared NPC result;
- `body`: bodily response, impulse, restraint, physical reaction, or movement
  remains character-owned even when a user-declared mental/speech outcome is
  followed;
- `both`: both layers receive character priority;
- `off`: user-declared NPC direction has greater priority, subject to hard
  world impossibility and applicable boundaries;
- sufficiently strong pressure can overcome a mind or body tendency when the
  character and situation logically support it.

This mode is a branch/turn control, not an intrinsic character trait. The
character dossier contains the traits and pressures used when applying it.

### Adult examples and craft retrieval

The old Adult `Off / On / EX` content toggle was designed before Pi-based
retrieval. It is now superseded.

DeepSeek should use a confined tool such as:

```text
get_craft_context(keywords, scene_tags, relationship_stage, limit)
```

Python performs a bounded indexed lookup over approved, separated example and
craft records. DeepSeek may identify relevant keywords and request a bounded
result. It must not browse arbitrary repositories, drives, other chats, or
manager files.

The review should design how example records are divided, tagged, indexed,
ranked, retrieved, cited, cached, and prevented from becoming story canon.

### Genesis, new chats, forks, and current state

- Genesis is the starting revision copied into a new world/branch for every new
  chat.
- A fork copies the complete accepted source branch and then diverges.
- Genesis contains both creator-locked and story-evolvable starting facts.
- A later accepted event may change an evolvable fact, such as age after a
  birthday, through an explicit supersession/evolution record.
- Rejected candidates never enter accepted state, inherited branches, current
  dossiers, or accepted provider-session lineage.

## What the current Codex Planner actually does

Python currently builds a structured semantic input containing much of the
following:

- exact current user source;
- known, accepted-present, and authorized-remote character IDs;
- current public scene state;
- immediate prior accepted ordinary sequence;
- character mappings/deltas;
- accepted evidence;
- durable changes;
- relationships;
- relevant memories;
- unresolved threads;
- hard boundaries;
- current autonomy controls.

The retained Codex Planner reasons over that input and returns a structured
`SequenceDraftV1` containing ordered semantic items, owners, causal-parent
links, evidence links, presence changes, durable changes, resulting public
state, unresolved threads, and a stopping boundary.

Python validates structure and authority. It does not decide the character's
meaning.

Provider cache only reuses previously processed tokens. The retained session is
a reasoning aid. Neither is story memory or canon. Python accepted branch state
must be sufficient to reconstruct the Planner after session loss.

### Current limitation requiring this review

The model internally decides why a character selected an action, but the
current sequence contract preserves mainly the selected semantic result and
causal links. It does not always preserve a compact, explicit explanation of:

- the character's active goal;
- the strongest pressures and conflicts;
- the materially plausible alternatives;
- why the selected response defeated those alternatives;
- what uncertainty remained;
- what immediate and longer-term consequences the character anticipated.

Do not request or store private chain-of-thought. Determine the smallest useful
decision-rationale record, such as a concise `decision_basis`, that makes CERA's
character choices auditable and reusable without creating Vera-style rigid or
exhaustive ledgers.

The live launcher also still relies on a fixed Hanezawa seed. The new
world-workspace manager, request-bound retrieval bridge, complete Current
Character Context, and Planner workspace binding are not fully integrated into
the live route.

## Current Character Context direction

One character ID should provide one logical, current, role-authorized lookup.
It does not have to be one physically enormous duplicated JSON file.

A complete logical character context may include:

- identity and Genesis facts;
- creator-locked versus evolvable fields;
- accepted character development;
- current physical state and bodily pressures;
- current emotional tendencies supported by accepted records;
- goals, conflicts, incentives, fears, and pressures;
- relationships and relationship-specific perspectives;
- knowledge, beliefs, suspicions, lies, and memories;
- current presence and scene condition;
- active durable effects;
- unresolved threads;
- voice and behavioral guidance;
- scene relevance;
- non-explicit adult continuity for Codex;
- protected adult references for authorized DeepSeek roles;
- evidence IDs, visibility, provenance, supersession, and accepted revision.

Python must bind the tool session's world, branch, accepted head, and role.
Models must not be allowed to choose a different `world_id`, `branch_id`, or
privileged `role` in tool arguments.

## Rational-processing questions for the Vera operator

Use Vera experience to answer all of the following.

### 1. Vera's actual cognition model

- What were Vera's real processing stages from user input to final character
  response?
- Which stage owned intent, motive conflict, action choice, bodily response,
  consequence prediction, participant selection, and stopping point?
- Which components performed genuine useful work, and which mostly duplicated
  or constrained another model?
- Which concepts worked in actual scenes rather than only looking good in a
  design document?

### 2. Meaningful decision formation

Design how CERA should answer:

```text
Given this character, accepted world, current scene, user direction,
relationships, memories, goals, autonomy mode, and bodily pressures:
what does the character realistically choose, why, and what follows?
```

Specify:

- the minimum input required;
- the internal ordering of evaluation;
- how conflicting goals and pressures are weighed;
- how mind and body interact;
- how the user prompt is treated under each autonomy mode;
- how uncertainty and incomplete evidence affect a choice;
- how strong but nonabsolute tendencies can be overcome;
- how impulsive versus reflective characters differ;
- how immediate reaction differs from later considered action;
- how the Planner avoids selecting only the most obvious or repetitive answer.

### 3. Decision output contract

Evaluate the current sequence-first DTO. Recommend the smallest structure that
preserves useful logic without exposing chain-of-thought or recreating an
exhaustive role ledger.

Consider whether each critical choice needs:

```text
owner_id
selected_intent
decision_basis
material_alternatives
active_goal_ids
pressure/evidence IDs
causal parent
anticipated consequence
confidence or uncertainty
surface response meaning
stopping boundary
```

State which fields are essential, optional, derived by Python, or harmful.

### 4. Multi-character reasoning

Explain how CERA should:

- determine who notices, reacts, speaks, waits, or remains backgrounded;
- preserve the conversational floor;
- avoid surfacing every known family member;
- handle simultaneous or sequential reactions;
- prevent one character from using another's private knowledge;
- let one action create a meaningful domino sequence across multiple scenes;
- stop at a natural reader-facing junction without prematurely handing every
  moment back to Ted.

### 5. Alternate outcomes

Define what makes an alternate Regenerate outcome legitimate.

It must not change a stable character answer merely to produce variety. If a
character would reliably refuse, another realization may express refusal
differently or choose another realistic compatible action, but cannot become an
implausible acceptance merely because the user regenerated.

Explain how the Planner distinguishes:

- one strongly dominant decision;
- multiple plausible decisions;
- presentation variation;
- a genuinely different plan requiring Replan;
- a Writer error requiring Repair.

### 6. Memory and current-state design

Compare Vera's memory design with CERA's intended architecture.

Recommend how to store and retrieve:

- immutable accepted visible prose;
- primary planned decisions;
- compatible secondary Writer additions;
- events and consequences;
- character-specific memories;
- knowledge and ignorance;
- beliefs, suspicions, lies, jokes, and public claims;
- relationships and asymmetric perspectives;
- physical/material state;
- transient versus durable emotion or condition;
- goals, promises, threats, commitments, and unresolved threads;
- supersession and resolution;
- provisional canon and scenes depending on it.

Specify what belongs in normalized authoritative records, generated current
read models, recent-prose windows, provider sessions, and debug evidence.

### 7. Precedence and contradiction resolution

Reconcile these concepts:

```text
creator-locked Genesis
evolvable Genesis starting facts
newly accepted primary records
older accepted records
accepted secondary records
provisional records
superseded records
```

Do not use a naive newest-wins rule for immutable identity. Do not let Genesis
prevent legitimate accepted story evolution. Define explicit precedence and
supersession rules.

### 8. Current dossier and retrieval design

Determine whether CERA should use:

- one physical file per character;
- one manifest plus referenced normalized sections;
- a materialized database view;
- or another rebuildable representation.

The model must experience one compact character lookup, while Python preserves
privacy, consistency, evidence, and atomic revision changes.

Design:

- the default `get_turn_context` result;
- focused `get_character_context` expansion;
- relationship and memory lookup;
- keyword/tagged craft-example retrieval;
- role-bound visibility;
- server-bound world/branch authority;
- byte and call limits;
- evidence references;
- atomic dossier generation and repair.

### 9. Validation versus reasoning

Explain the proper difference between:

- Codex Planner logic;
- Luna ordinary realization validation;
- DeepSeek ordinary prose realization;
- DeepSeek adult logic/prose;
- DeepSeek Adult Filter/Validator;
- ordinary Recorder;
- Python deterministic validation;
- Ted's creative judgment.

Identify any missing role and any role that should be removed. Do not add a
component merely because Vera used one.

### 10. Sessions, cache, and reconstruction

Recommend what accepted information may be retained in:

- the persistent Codex Planner session;
- the separate Luna session;
- Pi-hosted DeepSeek Writer sessions;
- adult Scene and Filter sessions;
- provider prefix cache.

Prove that session or cache loss changes latency only, not correctness.

### 11. Observability and quality evaluation

Specify a user-readable and developer-readable trace showing:

- exact accepted state/revision used;
- evidence retrieved;
- effective autonomy mode;
- concise decision rationale;
- chosen sequence;
- Writer realization;
- Validator result;
- acceptance or provisional disposition;
- committed records and refreshed dossier hashes;
- provider calls, latency, cache, and failures.

Do not log protected adult prose into ordinary Codex-readable locations.

### 12. Missing considerations

Independently identify anything Ted, Pro, or the current Codex review omitted.
Pay special attention to:

- long-running character development drift;
- temporary states accidentally becoming permanent traits;
- relationship duplication or disagreement across dossiers;
- forgotten private knowledge boundaries;
- stale current-state projections;
- fork and restart behavior;
- provisional-canon dependency propagation;
- multi-scene causal continuity;
- contradictory accepted records;
- user-control precedence;
- latency caused by oversized context or too many retrieval calls;
- validation that becomes so strict it destroys natural prose;
- stochastic model variation being mistaken for a backend defect.

## Current code paths to inspect

At minimum inspect:

```text
D:\Cera\worktrees\C78-quality-fixes\src\cera\pi_scene\codex_planner.py
D:\Cera\worktrees\C78-quality-fixes\src\cera\sequence_first\contracts.py
D:\Cera\worktrees\C78-quality-fixes\src\cera\sequence_first\prompting.py
D:\Cera\worktrees\C78-quality-fixes\src\cera\sequence_first\provider.py
D:\Cera\worktrees\C78-quality-fixes\src\cera\pi_scene\context.py
D:\Cera\worktrees\C78-quality-fixes\src\cera\pi_scene\writer_view.py
D:\Cera\worktrees\C78-quality-fixes\src\cera\pi_scene\world_workspace.py
D:\Cera\worktrees\C78-quality-fixes\src\cera\pi_scene\_branch_state_models.py
D:\Cera\worktrees\C78-quality-fixes\src\cera\pi_scene\_branch_state_reducer.py
D:\Cera\worktrees\C78-quality-fixes\src\cera\pi_scene\store.py
D:\Cera\worktrees\C78-quality-fixes\src\cera\pi_scene\runtime.py
D:\Cera\worktrees\C78-quality-fixes\src\cera\pi_scene\pi_adapter.py
D:\Cera\worktrees\C78-quality-fixes\src\cera\pi_scene\http_contracts.py
D:\Cera\worktrees\C78-quality-fixes\scripts\run_pi_scene_lean_server.py
```

Trace the actual current call path. Do not infer live behavior solely from
roadmap documents.

## Required output

Return one self-contained review with:

1. A concise reconstruction of Vera's original logical/rational-processing
   architecture, tied to exact Vera files and behavior.
2. What Vera did well and CERA should preserve in principle.
3. What Vera did poorly, overcomplicated, or coupled incorrectly.
4. A direct assessment of CERA's current Planner reasoning quality and whether
   its output proves a meaningful character decision.
5. A recommended character cognition and decision algorithm for CERA.
6. A compact recommended Planner input/output contract.
7. A complete accepted-state, event, memory, relationship, and current-dossier
   storage design.
8. Retrieval contracts and role/privacy boundaries.
9. Regenerate, Repair, Replan, provisional-canon, and automatic-Accept behavior.
10. Multi-character, multi-scene, restart, fork, and long-term-development
    simulations.
11. A comparison table:

    ```text
    Vera behavior
    Current CERA behavior
    Recommended CERA behavior
    Reason
    Migration impact
    ```

12. Specific changes required in the two August 9 roadmap documents.
13. A phased provider-free implementation roadmap that preserves the completed
    CERA custody work.
14. Tests proving meaningful reasoning rather than merely valid JSON.
15. Questions that genuinely require Ted's decision.
16. Anything important the current team missed.

## Review discipline

- Be candid and willing to reject both Vera and current CERA assumptions.
- Optimize for believable character decisions and maintainable long-term state,
  not maximum schema size.
- Do not request or preserve hidden chain-of-thought. Recommend concise
  decision summaries and evidence instead.
- Do not make Python infer narrative meaning.
- Do not make cache or retained sessions authoritative.
- Do not add exhaustive per-sentence roles, spans, or ledgers without concrete
  need.
- Do not let one character access another character's private knowledge.
- Do not let examples establish canon.
- Do not let generated current dossiers replace normalized accepted records.
- Do not weaken branch, privacy, accepted-state, restart, or creator-authority
  custody for convenience.
- Distinguish architectural defects from isolated stochastic provider output.
- Finish with a clear recommended direction, not merely observations.
