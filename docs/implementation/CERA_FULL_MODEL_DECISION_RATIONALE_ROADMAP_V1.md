# CERA Full-Model Decision-Rationale Roadmap V1

status: creator-directed implementation roadmap
owner: Ted
technical owner: Codex
date: 2026-08-09
implementation repository: `D:\Cera\worktrees\C78-quality-fixes`
documentation checkpoint at preparation: `5cbefd3563d3cec625a1cd3ab492b2bdf14418af`
runtime-source ancestor at preparation: `1cc8ff8cce3496b00bd1641d993b94427d6ea417`
provider authority from this document: none

## 1. Purpose

This is the descriptive roadmap Codex will use to build the complete CERA
model. It consolidates:

- Ted's creator decisions through 2026-08-09;
- the corrected ordinary and adult route design;
- the completed C78 runtime-quality work;
- the useful psychological and rational-processing principles recovered from
  Vera;
- prompt, retrieval, cache, latency, test, and maintainability requirements;
- the mistakes that caused earlier CERA and Vera implementations to become
  rigid, slow, or difficult to qualify.

It is intentionally a roadmap, not a provider-call authorization. Creating or
approving this document does not dispatch a model, mutate a live story, update
installed SillyTavern, deploy a service, merge a branch, push a remote, or
promote a production route.

The product objective is:

```text
The user supplies the initial domino.
CERA decides how the remaining character and world dominos realistically fall.
DeepSeek realizes their color, shape, texture, and presentation.
Python makes accepted state easy to retrieve and difficult to corrupt.
```

The central technical rule is:

```text
Models judge meaning.
Python binds authority, retrieval, identity, validation shape, and persistence.
Ted owns product direction and exceptional creator judgment.
```

### 1.1 Evidence basis

This roadmap reconciles the following review inputs without treating an older
proposal as automatic authority:

- Vera logic/rationality transcript SHA-256
  `9749a75792733419638181f469426a30b982e0129bd78591c5b8d5098992904a`;
- Vera contextual-recollection transcript SHA-256
  `d60523c8a4410f2261687dc712aca6248157a8bfeabcc477f07d0f5b8ad9c404`;
- `CERA_DECISION_RATIONALE_ROADMAP_2026-08-09.md` SHA-256
  `306d6df188d64523d619fe427aeaa8dc3d11ba99603d34c03e45d214e11e7436`;
- `CERA_ARCHITECTURE_AND_ROADMAP_RECONCILIATION_REVIEW_2026-08-09.md`
  SHA-256
  `283bbd8769aff285ea1cb4e3c22cf5f8eb2c51e2f2ee0b05e48576c5eecb70753`;
- the C78 source, tests, creator-decision record, reconciliation request, and
  current sequence contracts at the preparation checkpoint;
- Ted's direct corrections and final decisions in this task.

Direct creator decisions control conflicts. Vera supplies useful cognition
principles and historical lessons, not runtime code or numerical authority.

### 1.2 Creator-approved delta D-219 - 2026-08-10

This delta is controlling and must be implemented and provider-free qualified
before live qualification resumes.

| Decision | Change | Previous roadmap behavior | Creator-approved behavior | Implementation state |
|---|---|---|---|---|
| Ordinary Reader | Added/restored | Luna alone gated ordinary semantics and severe completeness | A separate fresh Reader gates severe visible quality | In progress |
| Validation scheduling | Changed | Luna completed before acceptance; no active Pi Reader | Luna and Reader run concurrently from the same frozen Writer candidate and never receive each other's verdict | In progress |
| Provisional review | Added | The synchronous route returned only after validation and automatic acceptance or rejection | Exact frozen prose is durably visible as provisional while independent verdicts are pending, with reload/restart recovery | In progress |
| Acceptance default | Clarified | Automatic Accept after Luna and Python checks | Automatic Accept after Luna, Reader, and Python checks; optional per-chat Manual Review requires explicit Accept | In progress |
| Rejection controls | Expanded | Semantic rejection exposed one validation result | Preserve exact prose, show every applicable concise Luna/Reader failure, and offer Regenerate, Decline, and auditable creator override | In progress |
| Adult quality floor | Clarified | Adult Filter owned semantic fidelity and protected record/projection staging | Adult Filter also owns the narrow severe-quality floor; no Codex Reader receives protected prose and no extra adult call is added | In progress |

This decision restores the separate Reader responsibility already implemented
and tested by the earlier Runtime Model V3 and Sequence-First paths. The active
Pi route must adapt those closed contracts to its own candidate, retry,
accounting, HTTP, and SillyTavern lifecycle rather than transplanting an older
runtime wholesale. Reader acceptance alone never creates canon.

### 1.3 Creator-approved delta D-220 - 2026-08-13

D-220 narrows only D-219's ordinary soft-rejection disposition. It does not
weaken Python, privacy, custody, adult, or hard semantic gates.

The immutable authority is `standing_creator_policy`
`ordinary_provisional_continuity` v1. Its exact policy text SHA-256 is
`1fe7bf05034f1543040eac456818269768760e58dc625eecc03508bd64a804e2`; its
canonical policy-object SHA-256 is
`47729a4fc27046e8da768c8e0f1bc6670be48e576e606f193339de30b3bf3b23`.

| Signal | Disposition |
|---|---|
| Luna `omitted_decision`, `presence_violation`, `stopping_boundary`, or `capability_restriction` | Soft |
| Reader rejection whose every issue scope is `exact_quote` or `omitted_planner_item` | Soft |
| Python failure or inconclusive | Hard |
| Any other Luna class | Hard |
| Reader inconclusive or any `whole_candidate` issue | Hard |

Hard wins whenever signals are mixed. With Python pass and only soft signals,
Python accepts the first exact Writer candidate exactly once as provisional
continuity. It makes no second Writer call and requires no manual creator
action. The Luna/Reader rejection evidence remains attached, and the accepted
artifact carries separate nonforgeable policy provenance. Adult remains strict
and unchanged. Ordinary review V2 is historical; V3 is current.
The matching current qualification contracts are manifest V24 over fixture
V20, phase result V6, and complete result V5. Historical manifest V23
introduced the separate standing-policy provisional counter and hash-only
evidence rather than automatic/manual acceptance relabeling.

This is controlling design authority. Source integration and focused
provider-free evidence are complete; the complete no-selector gate and fresh
fixture qualification remain pending. This text does not authorize a provider
call, live rerun, route promotion, or installed-client sync.

## 2. Supersession and preservation

### 2.1 This roadmap supersedes these assumptions

Where an older draft conflicts, this roadmap supersedes:

- explicit creator Accept as the normal success path;
- Regenerate as prose-only replay of an exact frozen sequence;
- Python semantic validation of ordinary prose;
- a post-Accept Adult Recorder duplicating the Adult Filter;
- `Adult OFF / ON / EX` as a route-selection system;
- current-character context assembled from a recent prose window;
- full character cards or world dumps repeated in every provider prompt;
- decimal psychological coefficients treated as runtime truth;
- a semantic rule or prompt patch for every isolated Writer mistake;
- a shared mutable world or provider session treated as durable story memory.

The normal accepted path is automatic after required semantic and deterministic
checks. A hard-failed candidate remains available for Ted to inspect and is not
accepted automatically. The one exception is D-220's ordinary soft-only first
candidate, which enters provisional continuity automatically under the frozen
standing policy and attached audit provenance.

Regenerate now repeats the same accepted state, exact user source, settings,
and evidence revision, but asks the logic owner to make a fresh realistic
decision. It may produce a different sequence only when that alternative is
genuinely plausible. It must not force an opposite result merely to create
variety.

### 2.2 Preserve completed work unless a focused regression proves otherwise

The following C78 foundations are valuable and must not be rebuilt casually:

- accepted receipt and branch-head custody;
- strict durable DTO decoding;
- exact accepted prose committed before optional derived recording;
- crash recovery and idempotent creator-decision replay;
- record, projection, and head hash verification;
- pending Recorder recovery without duplicate story acceptance;
- cumulative accepted branch-state reduction;
- route-specific ordinary and protected-adult continuity views;
- accepted-turn-bound record bundles;
- protected full adult record versus Codex-readable projection separation;
- per-chat Planner thread identity and tamper rejection;
- provider-free dispatch guard and provider accounting seams;
- new-chat Genesis isolation and complete branch-fork workspace primitives;
- request-bound, root-confined retrieval bridge primitives;
- local HTTP authentication, origin, content-type, body-size, and no-store
  controls;
- no silent provider fallback or recursive retry.

### 2.3 Current seams are not yet live features

The roadmap must not claim completion merely because an interface exists.
Known seams still requiring integration or requalification include:

- live launcher binding to per-chat world workspaces;
- request-bound world retrieval for the retained Planner;
- current SillyTavern controls reaching the exact runtime request;
- durable review, candidate, session, and provider-accounting recovery on the
  final route;
- adult projection V2 presence and scoped durable effects;
- complete Current Character Context materialization;
- the decision-rationale cognition contract;
- ordinary Luna validation;
- the separate Adult Filter/Validator;
- automatic one-owner route selection;
- the final isolated six-message and twenty-turn campaigns.

## 3. Fixed product decisions

### 3.1 World, chat, Genesis, and fork semantics

- A new SillyTavern chat creates a new world and branch workspace.
- It starts from the newest accepted Genesis revision available at creation.
- An existing chat stays pinned to its installed Genesis revision and accepted
  branch state.
- A branch fork copies the selected source branch at the fork point, including
  accepted records, current state, pending recording state, provisional
  dependencies, settings, and lineage, then receives a new branch identity.
- Rejected candidates never enter a new chat or fork.
- Runtime models may access only the assigned world and the role-authorized
  shared Genesis dependencies pinned into that world.

Genesis is immutable historical input, not an eternally dominant current-state
table. Creator-locked identity and lore remain locked. Evolvable facts change
through accepted story events while their original Genesis bytes remain
historically intact.

Examples of creator-locked categories include:

- identity and biological/legal family relationships;
- birth and origin facts unless Ted explicitly corrects the source;
- creator-declared immutable lore;
- the existence and identity of foundational characters.

Examples of evolvable categories include:

- current age after accepted passage of time or a birthday;
- relationships, beliefs, goals, skills, health, presence, residence, and
  current conditions;
- learned information and remembered experiences;
- preferences or tendencies supported by accepted development.

Concrete-fact correction is critical. If Ted writes a narrative assertion that
contradicts locked authority, CERA preserves the literal source but rejects the
asserted fact and plans from the authoritative state. It exposes the correction
in the collapsed reasoning trace. If Ted says the same false statement as
in-character dialogue, the utterance remains a potentially false, mistaken, or
deceptive claim; it does not overwrite objective canon.

### 3.2 One logic owner per user prompt and visible candidate

```text
ordinary prompt -> Codex owns complete ordinary logic
adult prompt    -> DeepSeek owns complete adult logic and prose
```

A candidate cannot change logic owner midway. Route detection or a typed
handoff may happen before visible generation. `return_to_codex` affects the
next user prompt only.

For a mixed prompt that crosses the adult-route boundary, the entire visible
candidate is DeepSeek-owned. Codex may return a pre-generation typed handoff,
but it must not author part of the visible scene first.

### 3.3 Character autonomy

CERA exposes one chat-wide setting:

```text
off | mind | body | both
```

The initial default is `both`.

- `off`: user-declared NPC outcomes normally lead, subject to hard world and
  creator-authority contradictions.
- `mind`: character beliefs, judgment, interpretation, intent, speech, and
  voluntary choice take precedence over the user's proposed outcome.
- `body`: character physical feeling, involuntary response, feasibility,
  reflex, hesitation, recoil, urge, and movement take precedence.
- `both`: independent character judgment and bodily response both take
  precedence.

These are precedence settings rather than absolute switches. Sufficiently
strong, evidence-supported pressure can overcome a tendency. The logic owner
must explain the material interaction instead of applying a rigid clause rule.

### 3.4 Acceptance, repair, and provisional canon

Normal candidate disposition is:

```text
Python pass + Luna pass + Reader pass
-> automatic Accept, or explicit Accept in Manual Review mode

Python pass + at least one rejection + only D-220 soft signals
-> standing-policy Accept exactly once as provisional continuity

any Python non-pass or any hard Luna/Reader signal
-> hard rejection
```

Critical authoritative defects may receive one complete automatic repair. A
repair is a complete replacement, not an edited fragment. D-220 soft signals
never enter that repair path: the first exact Writer candidate is retained and
accepted provisionally without a second Writer call or a manual action. If a
hard repair fails, or the problem is not an automatically repairable hard
defect, CERA shows the candidate, the replacement when present, and concise
findings to Ted.

Ted may then:

- Decline;
- Regenerate;
- Replan where applicable;
- accept a failed candidate explicitly as provisional canon.

A candidate accepted provisionally receives a durable provisional ID. Every
later scene that relies on it carries that ID and becomes provisionally
dependent. The first chosen working assumption for a dependent lineage remains
pinned through that lineage so CERA does not alternate between treating the
same event as true and false on consecutive turns. Later confirmation,
correction, rejection, or supersession updates every dependency explicitly.

Standing-policy acceptance is not an implicit validator pass or a rewritten
verdict. The current V3 review and accepted receipt retain the original
Luna/Reader rejection hashes and a separate policy audit bound to the candidate,
Python qualification, immutable policy object, and exact soft reason codes.
Any unbound, mismatched, client-authored, or incomplete provenance fails closed.

### 3.5 Regenerate and Replan

`Regenerate` means:

```text
same accepted head
+ same exact user source
+ same world and branch
+ same autonomy, depth, route, and evidence revision
+ no automatic failure feedback
-> fresh logic-owner judgment
-> another realistic complete candidate
```

The result may remain similar when one outcome is strongly dominant. A
different choice is permitted only when it is realistically available to the
character. Optional creator guidance may be attached; the logic owner still
rejects guidance that would require an implausible character outcome.

`Replan` means that the prior reasoning is being challenged or deliberately
changed. It uses an open text box, which may be empty, and creates a new
decision/sequence identity. Validator findings may propose Replan, but they do
not silently rewrite the plan.

### 3.6 Visibility and controls

SillyTavern must provide collapsible views for:

- every relevant character decision;
- decision basis and evidence references;
- autonomy application;
- material conscious and subconscious associations;
- route owner and route transitions;
- validation state and concise conflicts;
- removed/replaced candidates;
- provisional dependencies;
- Recorder and projection status;
- a user-readable debug-log path.

Ted may see character-private and protected creator diagnostics through the
appropriate local protected view. Ordinary provider roles receive only their
authorized projections.

## 4. Provider-neutral role architecture

Core business logic must depend on ports, not provider product names.

| Core port | Initial adapter | Owns | Cannot own |
|---|---|---|---|
| `CharacterLogicPort` | retained branch-bound Codex Planner | ordinary perception, appraisal, motive conflict, decisions, causal sequence, participants, stopping point | visible prose, adult protected realization, canon writes |
| `SceneWriterPort` | Pi-hosted DeepSeek Flash non-thinking | ordinary prose, dialogue wording, staging, atmosphere, pacing, compatible secondary detail | critical ordinary decisions, acceptance, state writes |
| `SemanticValidatorPort` | separate branch-bound ChatGPT Luna Extra High | ordinary decision-to-prose fidelity and severe completeness | new planning, prose rewrite, canon writes |
| `ReaderPort` | fresh isolated Codex Sol Medium | severe reader-facing repetition, voice, pacing, readability, depth, and stopping failure | semantic authority reinterpretation, prose rewrite, canon writes |
| `AdultScenePort` | Pi-hosted DeepSeek | complete adult logic and prose | independent self-approval, canon writes |
| `AdultFilterPort` | separate Pi-hosted DeepSeek Filter/Validator | adult semantic fidelity, protected record, safe projection, next-route proposal | prose rewrite, canon writes |
| `RecorderPort` | ordinary DeepSeek Recorder | future-relevant secondary continuity from accepted ordinary prose | candidate approval, primary decision replacement |
| deterministic services | Python | route custody, world/branch/head, indexes, visibility, schemas, hashes, transactions, recovery, provider accounting, D-220 hard/soft policy evaluation and provenance, read-model generation | psychology, social meaning, character voice, prose quality |
| creator interface | Ted through SillyTavern | product settings, hard-rejection controls, exceptional provisional override, Decline, Regenerate, Replan, inspection | mechanical identity, persistence, or fabrication of standing-policy acceptance |

Provider sessions are soft continuity. Prefix cache is performance. Python's
accepted branch state is authority.

## 5. Ordinary cognition and rational-decision protocol

### 5.1 Why CERA needs this protocol

The current Planner prompt asks for motives, alternatives, rationale, choice,
consequence, and stopping. The structured output does not require those parts
to be represented distinctly. A powerful model may reason deeply, but it may
also jump from context directly to a plausible-looking action. Cache and a
persistent session do not prove a decision process occurred.

CERA therefore needs a compact, auditable decision protocol. It must expose
enough reasoning to test character logic without storing hidden chain-of-
thought or rebuilding Vera's numerical middleware.

### 5.2 Per-turn reasoning sequence

The retained Codex Planner performs these stages inside one provider operation,
using bounded tools as needed:

1. **Bind source and authority.**
   - Identify literal user dialogue, narration, proposals, attempts, desired
     outcomes, and uncertain claims.
   - Preserve the exact source.
   - Detect locked-fact contradictions.
   - Separate false in-world dialogue from false narrative authority.

2. **Select materially relevant actors.**
   - Start from accepted presence, current floor, addressed participants, and
     the event's likely observers.
   - Do not surface a character merely because a name appears in an old record.
   - Do not require every present character to react.

3. **Build an observer frame for each relevant actor.**
   - What did the character directly perceive?
   - What could they infer, and with what certainty?
   - What remains private, hidden, ambiguous, or outside their attention?
   - Which preceding draft-local actions have already changed the scene?

4. **Retrieve focused evidence.**
   - Current character context.
   - Relevant goals, relationships, beliefs, memories, conscious associations,
     subconscious pressures, physical state, commitments, and unresolved
     threads.
   - Exact records for any hard factual decision.
   - Retrieval omissions and budget exhaustion remain visible as uncertainty;
     absence from a limited result is not proof of character ignorance.

5. **Appraise contextual and personal meaning.**
   - Literal event and speech act.
   - Perceived intent and certainty.
   - Personal relevance and social context.
   - Activated memories or associations.
   - Immediate bodily response.
   - Emotional and relational meaning.
   - Effects on goals, safety, autonomy, trust, status, intimacy, duty, or
     uncertainty.

6. **Separate response layers.**
   - Immediate involuntary reaction.
   - Conscious interpretation.
   - Subconscious pressure.
   - Considered judgment.
   - Selected intention.
   - Visible speech or action.

   These layers may disagree. A bodily response does not establish mental
   intent. A thought does not erase physical limits. An internal feeling does
   not force its visible expression.

7. **Apply autonomy and competing pressures.**
   - Enforce hard knowledge, identity, branch, physical, and creator locks.
   - Apply `off / mind / body / both` precedence.
   - Compare goals, commitments, relationships, memories, current emotions,
     body pressure, anticipated consequences, and uncertainty.
   - Treat story momentum only as a tie-breaker among realistic choices.

8. **Generate alternatives adaptively.**
   - Do not require a fixed number.
   - When one decision strongly dominates, select it without manufacturing an
     artificial alternative.
   - When another materially plausible decision is close, preserve one concise
     alternative and why it lost or remained available.

9. **Select the decision and advance draft-local state.**
   - Record the chosen intent, concise basis, anticipated immediate effect, and
     uncertainty.
   - Apply the planned action to candidate-local state only.
   - Reevaluate every remaining relevant character after each material action.
   - Prevent duplicate interventions caused by actors reasoning from the same
     stale scene.

10. **Select depth and stopping boundary.**
    - `Auto / Short / Medium / Long / Epic` controls meaningful causal scope,
      not decorative word padding.
    - Stop at a natural reader-facing junction.
    - Do not stop merely because Ted could theoretically respond.
    - Do not make a new consequential decision for Ted.

11. **Return one decision bundle.**
    - Material decisions.
    - Ordered causal sequence.
    - Presence and public-state changes.
    - Proposed durable changes.
    - Unresolved threads.
    - Route transition proposal when applicable.
    - No final prose and no persistence instructions.

### 5.3 Pressure representation

Runtime psychology uses qualitative, evidence-linked pressures:

```text
none | low | moderate | high | overwhelming
```

Example:

```json
{
  "kind": "autonomy_threat",
  "level": "high",
  "direction": "resist",
  "evidence_refs": ["event:turn-18", "memory:sakura-42"]
}
```

Do not store provider-authored decimal coefficients as psychological truth.
Python may map categories to numbers for offline telemetry or evaluation, but
the mapped number does not become canon and does not replace evidence.

### 5.4 Decision contract

Add a provider-authored `DecisionRecordV1` table to the existing sequence
result instead of replacing `SequenceDraftV1` or duplicating every sequence
field.

Suggested minimal shape:

```json
{
  "decision_key": "sakura_door_response",
  "owner_id": "character:sakura_hanezawa",
  "causal_trigger_item_key": "ted_knocks",
  "perceived_event_meaning": "An unexpected visitor is requesting entry.",
  "knowledge_certainty": "moderate",
  "selected_intent": "Verify the visitor before changing the access boundary.",
  "concise_decision_basis": "Household protection and uncertainty outweigh social pressure to admit him immediately.",
  "decisive_factor_refs": ["goal:protect_household", "fact:visitor_unverified"],
  "material_pressures": [],
  "autonomy_application": "both: user direction treated as proposed outcome",
  "anticipated_immediate_effect": "The threshold remains closed while Sakura asks a verifying question.",
  "close_alternative": null,
  "uncertainty": "moderate"
}
```

A decision record is required when an independently controlled character:

- makes a material choice;
- overrides or materially changes user-declared direction;
- changes a relationship, boundary, commitment, route, or durable fact;
- creates a future-relevant consequence;
- takes the conversational floor in a multi-character conflict.

Decorative gestures, incidental movement, and purely stylistic Writer details
do not require decision records.

Each material `SequenceItem` references one `decision_key`. A single decision
may authorize several causally related items. `selected_intent` explains why;
sequence items explain what occurs. Neither becomes a second writable copy of
the other.

### 5.5 What remains ephemeral versus durable

Ephemeral Planner working state includes:

- the full list of considered alternatives;
- every minor pressure or emotional fluctuation;
- private internal deliberation not needed by later turns;
- search paths and discarded relevance hypotheses.

Accepted durable state may include:

- the selected material decision;
- concise basis and decisive evidence references;
- material conscious or subconscious effects;
- resulting event, relationship, knowledge, physical, goal, or thread changes;
- the exact accepted visible prose and acceptance receipt.

This is auditable reasoning, not hidden chain-of-thought storage.

## 6. Meaning, memory, and character development

### 6.1 A word or action is an event, not a keyword command

CERA must process:

```text
accepted utterance/action
-> contextual meaning
-> character-specific appraisal
-> conscious interpretation
+ subconscious association
+ bodily and emotional response
-> selected decision
-> accepted event and future effects
```

The same phrase may be affectionate, sarcastic, threatening, patronizing,
sexual, playful, or irrelevant depending on speaker, listener, relationship,
tone, audience, scene, prior events, and uncertainty. Do not implement
`if phrase == X: add anger` rules.

### 6.2 Normalized record types

Use small typed records rather than one universal memory object:

- `AcceptedEventRecord` — what materially happened and who could observe it;
- `AcceptedClaimRecord` — what someone said, including truth status when known;
- `DecisionRecord` — selected character logic and concise basis;
- `BeliefRecord` — owner-specific proposition, certainty, and evidence;
- `ConsciousMemoryRecord` — owner, event, interpretation, accessibility, and
  emotional significance;
- `AssociationTraceRecord` — owner, cue, context, response tendency, formation
  evidence, salience, habituation/sensitization state;
- `RelationshipRecord` — directional trust, safety, intimacy, conflict,
  commitments, and evidence;
- `PhysicalStateRecord` — condition, cause, severity category, duration, and
  resolution;
- `TransientStateRecord` — temporary emotions or pressures with resolution or
  decay conditions;
- `GoalRecord` — active, partial, satisfied, blocked, abandoned, or superseded;
- `ThreadRecord` — unresolved story or relationship matter;
- `ProvisionalCanonRecord` — proposition, assumed state, source failure,
  dependencies, and resolution;
- `SupersessionRecord` — correction, evolution, refinement, resolution, or
  branch divergence.

Each durable record carries subject/owner, visibility, source event, accepted
turn, branch, revision, provenance, and supersession links. Exact prose remains
in the accepted story artifact and is referenced rather than copied into every
derived record.

### 6.3 Memory-encoding gate

Do not create durable memory for every sentence. Future relevance depends on:

- emotional or bodily intensity;
- novelty or unexpectedness;
- repetition;
- active-goal relevance;
- importance of the speaker or relationship;
- public versus private context;
- confirmation or contradiction of a belief;
- accepted physical, relationship, knowledge, or commitment consequences;
- later reinforcement or repair.

One event may create no durable record, one episodic memory, or several linked
effects. One event must not automatically create a permanent trait, preference,
attraction, aversion, willingness, trauma pattern, or generalized behavior.

Repeated evidence may create a tendency or association. Stable trait promotion
requires explicit review rather than automatic accumulation.

### 6.4 Conscious and subconscious effects

Subconscious is an operational story model, not a claim of neurological
simulation. It means a prior accepted event influences attention, expectation,
body response, hesitation, attraction, aversion, or interpretation without the
character consciously recalling the source.

Subconscious influence:

- never grants factual knowledge;
- never proves desire, permission, or intent;
- may be countered by current evidence;
- may habituate, sensitize, be masked, or be reinterpreted;
- remains visible to Ted in a collapsed protected character-state view.

### 6.5 Current character context

Python generates one complete protected current dossier per character and
branch. One character ID must provide the complete current, role-authorized
view needed by the caller rather than forcing file-by-file discovery.

The protected dossier includes references to:

- identity and installed Genesis revision;
- creator locks and evolvable baseline fields;
- accepted decisions and development;
- current physical and transient state;
- conscious memories, beliefs, and knowledge;
- subconscious associations;
- directional relationships and commitments;
- goals, threads, presence, route state, and scene relevance;
- autonomy setting;
- voice guidance;
- adult-safe continuity and protected adult references;
- evidence and supersession indexes.

The dossier is a generated read model. It is revisioned, hash-bound,
role-filtered, deletable, and rebuildable. It never becomes a second authority
database.

## 7. Retrieval, tools, and prompt efficiency

### 7.1 World-local and Genesis-pinned access

Runtime Codex receives a small Python direction packet plus bounded search and
fetch tools over the assigned world only. It does not receive unrestricted
filesystem access.

The global voice/craft/adult-example library is treated as a Genesis-owned,
versioned dependency. Each world pins its manifest, revision, hashes, and
allowed namespaces. The content may remain in one content-addressed read-only
store instead of duplicating every byte into every world. This preserves
reproducibility without allowing cross-world story-state access.

Character and story facts remain branch-local. Craft examples are technique
references only and can never establish canon, preference, knowledge, consent,
or an event.

### 7.2 Primary model tools

Most turns should begin with one batched call:

```text
get_turn_context
```

Focused expansion tools are:

```text
get_character_context
search_evidence
get_exact_record
get_relationship_context
get_memory_context
get_thread_context
get_voice_examples
get_craft_context
```

Internal Python packet builders are:

```text
build_luna_validator_packet
build_adult_filter_packet
build_ordinary_recorder_packet
```

Every public tool binds server-side:

- world, branch, accepted head, role, and request identity;
- allowed sections and visibility;
- byte, record, and tool-call budgets;
- deterministic ordering;
- evidence IDs and source revisions;
- stale-result rejection;
- no rejected-candidate or sibling-branch data.

Python may mechanically seed retrieval using identity, presence, current floor,
relationship pair, unresolved status, route, record type, recency, and accepted
revision. It must not decide psychological relevance. The logic owner may
expand the query when the seed is insufficient.

### 7.3 Prompt construction

Order prompts for stable-prefix caching:

```text
stable role contract
-> stable tool schemas
-> stable output contract
-> accepted branch checkpoint reference
-> dynamic route and authority
-> compact retrieved context
-> exact current source or candidate
```

Do not repeat stable instructions in every dynamic packet when the retained
role session already has an exact compatible contract. Every request still
binds contract version and accepted head so stale session text cannot override
current authority.

Prompt-size rules:

- Send a compact turn manifest, not the complete world.
- Retrieve complete current information for selected characters through one
  character ID view.
- Include exact record bodies only when needed for a hard decision.
- Prefer evidence references plus short accepted summaries for ordinary
  continuity.
- Keep protected adult examples and prose out of ordinary roles.
- Keep rationale only for material decisions.
- Do not add phrase-specific corrections for isolated Writer variation.
- Do not ask providers to echo IDs, hashes, paths, transactions, or custody
  fields Python can attach deterministically.
- Do not send exhaustive seven-role ledgers for every span.
- Do not preserve rejected text merely to improve a cache hit.

### 7.4 Session policy

- Codex Planner: retained per accepted branch.
- Luna Validator: separate branch-bound accepted checkpoint; candidate-specific
  child or fresh compact rehydration so rejected prose is discarded.
- Ordinary Writer: accepted-only Pi lineage when clean forking is proven;
  otherwise fresh rehydrated candidate session.
- Adult Scene and Adult Filter: separate protected role/session identities.
- Recorder: accepted-turn-bound and reconstructible.

Session loss may change latency and stylistic continuity. It must not change
facts, permissions, knowledge, route, accepted decisions, or branch state.

## 8. Ordinary runtime route

```text
1. SillyTavern submits exact user source and controls.
2. Python binds chat, world, branch, head, route, autonomy, depth, prompt mode,
   and request identity.
3. Retained Codex Planner runs the cognition protocol and bounded retrieval.
4. Codex returns DecisionRecords + SequenceDraft + proposed state effects.
5. Python validates schema, evidence existence, role visibility, ownership,
   branch/head identity, and deterministic constraints.
6. Python builds the confined Writer view.
7. Pi-hosted DeepSeek writes complete prose.
8. Python freezes the exact Writer prose and durably exposes it as PROVISIONAL.
9. Luna and Reader receive independent packets for that same frozen candidate
   and run concurrently. Neither packet contains the other role's verdict.
10. Luna checks material semantic fidelity. Reader checks only the narrow severe
    reader-quality floor: repetition, voice, pacing, readability, depth, and
    stopping failure.
11. Python durably binds both verdicts and revalidates candidate identity,
    deterministic rules, custody, and privacy.
12. With Python pass, Luna pass, and Reader pass, automatic mode performs one
    atomic Accept; per-chat Manual Review waits for explicit creator Accept.
13. With Python pass and at least one rejection, Python evaluates the immutable
    D-220 policy. Only the four exact Luna classes and Reader issues whose every
    scope is `exact_quote` or `omitted_planner_item` are soft. A soft-only first
    candidate is accepted exactly once as provisional continuity, without a
    second Writer call or creator action. Original rejection evidence remains.
14. Python non-pass, any other Luna class, Reader inconclusive, or any
    `whole_candidate` issue is hard. Hard wins over soft and retains the
    D-219 hard-rejection controls; it cannot be converted by the client.
15. Ordinary Recorder derives future-relevant secondary continuity only after
    acceptance and at most once.
16. Python validates and attaches the record bundle.
17. Python incrementally rebuilds affected dossiers and indexes.
```

Luna checks material meaning, not wording. It rejects omitted decisions,
contradicted motive or outcome, unauthorized consequential additions, knowledge
or presence violations, invented Ted dialogue/private state, critical factual
contradictions, and severe incompleteness. It permits compatible gestures,
interiority, dialogue wording, chronology, atmosphere, staging, and harmless
interpolation.

On a hard critical failure, one complete repair may be attempted automatically.
On continued hard failure, both candidates and the findings remain visible.
Ted can Decline, Regenerate, Replan, or accept provisionally when separately
authorized. D-220 soft rejection never makes that repair call and never waits
for a creator click.

Recorder failure does not erase accepted prose. The branch becomes visibly
`recording_pending`; dependent derived fields remain non-authoritative until
repair. Recorder repair never regenerates or recommits the scene.

## 9. Adult runtime route

### 9.1 Route selection

Python uses accepted typed route state and explicit creator controls first. If
an ordinary Planner determines that the current message requires the adult
logic owner, it returns `adult_handoff_required` before creating a visible
ordinary candidate. Python then routes the entire message to DeepSeek.

This pre-generation handoff is routing, not split scene authorship. If no role
can produce a valid typed transition, CERA returns a visible route error rather
than silently changing providers or manufacturing a partial scene.

### 9.2 Adult Scene and Filter

```text
1. Python binds protected branch authority and role-scoped retrieval.
2. Pi-hosted DeepSeek Adult Scene performs the same functional cognition
   stages as Codex and produces the complete adult logic and prose.
3. Prose appears as PROVISIONAL.
4. A separate Pi-hosted DeepSeek Adult Filter/Validator receives the candidate,
   adult authority, relevant protected accepted state, and autonomy controls.
5. The Filter returns pass/reject, one concise material conflict, a staged
   protected full record, a staged Codex-readable projection, and staged
   next-route state.
6. Python validates candidate binding, schema, event links, hashes, privacy,
   and transaction preconditions.
7. Pass -> automatic atomic promotion of exact prose, full record, projection,
   and route state.
8. Python rebuilds protected and safe current dossiers.
```

The Filter is a separate semantic role, not a censorship keyword layer and not
a second Adult Planner. It verifies that the candidate's chosen logic, body and
mind states, knowledge, agency, boundaries, consequences, and route transition
remain internally coherent. It does not rewrite the prose.

The protected full record may preserve complete adult continuity. The
Codex-readable projection must preserve material causal order, participants,
accepted decisions, emotional and relationship effects, knowledge and presence
changes, physical conditions relevant to later reasoning, unresolved matters,
and the route-exit decision without exposing protected exact prose.

Future DeepSeek adult reasoning may retrieve the protected record. Future
Codex/Luna ordinary reasoning receives only the projection and opaque protected
references.

### 9.3 Adult craft retrieval

The former `Adult OFF / ON / EX` prompt-packet design is replaced by tool-based,
keyword- and concept-indexed craft retrieval from the Genesis-pinned global
library.

The UI may retain an `Adult EX` breadth control, but it affects only how much
extended craft/example material the Adult Scene may retrieve. It never removes
core character, accepted continuity, relationship, body/mind, or route state,
and it never selects the logic owner.

## 10. Canon, precedence, and state reduction

Authority is type-aware and temporal:

1. branch identity, creator locks, and hard custody;
2. installed Genesis baseline;
3. accepted explicit corrections and evolutions in branch order;
4. accepted primary decisions and events;
5. compatible accepted Writer secondary continuity;
6. pinned provisional assumptions and their dependent lineage;
7. generated dossiers, summaries, indexes, and provider sessions.

This is not a simple global `newest wins` rule. A newer record supersedes an
older one only when it validly changes the same fact or state. Facts, claims,
beliefs, suspicions, lies, memories, and associations remain distinct.

The deterministic branch reducer produces:

- current public state and presence;
- character current state;
- directional relationships;
- knowledge and belief views;
- goal and thread state;
- physical and transient conditions;
- conscious memory and subconscious association indexes;
- provisional dependencies;
- route state;
- generated role views.

Recent prose is narrative context only. It cannot replace cumulative state.

## 11. SillyTavern creator experience and observability

### 11.1 Required controls

- backend/profile identity;
- autonomy: `off / mind / body / both`;
- depth: `Auto / Short / Medium / Long / Epic`;
- prompt adjustment/guidance;
- adult craft breadth;
- visible ordinary/adult logic-owner indicator;
- Accept as Provisional for failed candidates;
- Decline, Regenerate, Replan;
- manual Retry for a proven zero-effect provider transport failure;
- retry validation/filter only when no durable result exists;
- collapsed decision and state views.

Transport Retry is not Regenerate and is never automatic. It is exposed only
after Python has durably proven that the failed provider attempt created no
candidate, review, accepted receipt, recording, or selected-head effect. It
reuses the exact normalized user request and branch authority with a fresh
provider thread, preserves and charges the failed attempt, and creates a new
attempt identity. A running, pending, ambiguous, or story-effective operation
cannot be retried. The UI must not offer Retry merely because three minutes
have elapsed while an operation may still be running.

The frozen qualification may exercise this control only after a naturally
occurring, closed ordinary-Planner transport failure. It first reconciles the
exact Retry identity through authenticated GET and sends one explicit
empty-object POST for that identity. If the replacement Planner also has a
closed zero-effect failure, qualification may follow the one backend-issued
successor identity and send one final explicit empty-object POST: three Planner
attempts total, with no fourth attempt or fallback. Authenticated GET is the
only authority after an ambiguous POST result. After the third failed attempt,
GET must return the closed `attempts_exhausted` status with no successor action,
and SillyTavern shows the hash-safe Codex/Planner critical error in a collapsible
view with Retry disabled. Writer, Luna, Adult Scene, Adult Filter, and Recorder
failures are never transport-retried. A proven local pretransport failure is
retained in the evidence chain but consumes zero submitted and zero charged
provider calls; a submitted failed Planner attempt remains charged. The live
campaign does not inject a failure merely to exercise Retry.

### 11.2 Required user-visible states

```text
planning
writing
provisional_visible
validating
critical_repairing
auto_accepted
recording_pending
recorded
validation_failed
provisional_dependency
declined
regenerating
replanning
route_transition_pending
error_recoverable
transport_failed_retryable
transport_retrying
provider_stage_retry_exhausted
```

Every error shows:

- stable error code;
- stage and role;
- whether a provider operation was submitted;
- whether a candidate/review/accepted effect exists;
- review and candidate identity when safe;
- one recommended next action;
- one local user-readable debug-log path.

### 11.3 Debug bundle

Create one human-readable turn directory containing redacted role-separated
files:

```text
TURN_SUMMARY.md
01_INGRESS.json
02_RETRIEVAL_MANIFEST.json
03_DECISION_RECORDS.json
04_SEQUENCE.json
05_WRITER_REQUEST_SUMMARY.md
06_WRITER_RESULT.md
07_VALIDATOR_RESULT.md
08_ACCEPTANCE_AND_RECORDING.md
09_TIMING_AND_USAGE.json
```

Protected adult exact material remains in a separately permissioned protected
subdirectory. Ordinary summaries contain only hashes and safe projections.

## 12. Maintainable code and contract layout

### 12.1 Module boundaries

Prefer small composable packages:

```text
cera/cognition/
  contracts.py
  authority.py
  observer.py
  decision_bundle.py
  validation.py

cera/context/
  contracts.py
  dossier.py
  retrieval.py
  visibility.py
  indexes.py

cera/runtime/
  routing.py
  ordinary.py
  adult.py
  lifecycle.py
  review.py

cera/records/
  events.py
  decisions.py
  memories.py
  relationships.py
  provisional.py
  reducer.py

cera/providers/
  ports.py
  codex_logic.py
  deepseek_writer.py
  luna_validator.py
  deepseek_adult.py
  deepseek_filter.py
  recorder.py
```

These names are design targets, not mandatory wholesale moves. Extract modules
incrementally when a current file demonstrably mixes responsibilities.

### 12.2 Versioning and ownership

- Provider-authored semantic DTOs contain only semantic fields.
- Python custody envelopes contain IDs, hashes, paths, revisions, attempts,
  transactions, and provider receipts.
- Never change the meaning of an existing schema version in place.
- Decoders are closed and reject wrong types rather than coercing them.
- Generated read models carry source revision and hash.
- Every persistent operation is idempotent and restart-reconcilable.
- Core ports remain provider-neutral.

### 12.3 Tooling quality gate

Before broad feature integration, add or freeze reproducible:

- checkout/import-root assertion;
- Python compile gate;
- formatter configuration;
- linter configuration;
- type-check configuration for new and touched modules;
- focused unit/integration commands;
- provider-dispatch kill switch for offline tests;
- exact commit/tree/runtime-profile reporting;
- CI or one-command local equivalent.

Tests must not silently import another CERA checkout through a shared editable
virtual environment.

## 13. Implementation phases

Each phase ends with a clean checkpoint. Provider calls remain zero through the
provider-free freeze.

### Phase 0 — Freeze authority, baseline, and migration inventory

**Rationale:** implementation cannot be trusted while an older queue, source,
or route is still being treated as current.

Work:

1. Bind this roadmap and the final creator decisions.
2. Record exact execution commit, tree, branch, worktrees, active services, and
   installed SillyTavern integration hashes.
3. Classify every post-`1cc8ff8` source and documentation change as preserve,
   integrate, correct, supersede, or defer.
4. Confirm no historical evidence or accepted story state will be rewritten.
5. Freeze provider ceilings at zero.

Exit gate:

- one authoritative source checkpoint;
- clean tracked worktree;
- no unexplained active-runtime mismatch;
- one migration inventory;
- no provider operation.

### Phase 1 — Reproducible engineering and contract foundation

**Rationale:** new cognition work must not be added to a checkout that can test
the wrong source or silently coerce malformed durable state.

Work:

1. Establish checkout-pinned test execution and one local quality command.
2. Add formatter, linter, and bounded type-check configuration.
3. Preserve and extend strict DTO decoding.
4. Freeze provider-neutral ports and custody/semantic separation.
5. Define `DecisionRecordV1`, decision-bundle linking, qualitative pressure,
   route handoff, provisional dependency, and rationale trace contracts using
   provider-free fixtures.

Rejected shortcut: implementing new prompts before the contracts and source
root are reproducible.

Exit gate:

- compile, formatting, lint, type, contract, and compatibility gates pass;
- historical DTOs remain readable;
- no runtime route changed.

### Phase 2 — World workspace, accepted state, and Genesis dependencies

**Rationale:** realistic reasoning fails if the model sees stale, incomplete,
or cross-world state.

Work:

1. Wire the world-workspace manager into new-chat, open-chat, fork, restart,
   and launcher paths.
2. Materialize newest accepted Genesis on new chat.
3. Pin global craft/voice/adult libraries as content-addressed Genesis
   dependencies.
4. Complete normalized accepted event, claim, decision, belief, memory,
   association, relationship, physical, transient, goal, thread,
   provisional, and supersession records.
5. Complete deterministic branch reduction and checkpoints.
6. Preserve accepted-pending Recorder semantics without treating incomplete
   derived state as hard truth.
7. Complete adult projection V2 state effects.

Exit gate:

- new chat isolates Genesis and state;
- fork inherits complete accepted and provisional lineage;
- restart reconstructs the same authoritative state;
- no recent-prose dependency for durable truth;
- no protected adult leakage.

### Phase 3 — Current Character Context and bounded retrieval tools

**Rationale:** the cognition protocol must receive complete relevant character
state without repeating the complete world in every prompt.

Work:

1. Generate protected per-character current dossiers.
2. Generate role-specific views.
3. Implement `get_turn_context` as the default batched tool.
4. Implement focused character, exact evidence, relationship, memory, thread,
   voice, and craft tools.
5. Wire the request-bound bridge into the retained Planner and Pi roles.
6. Enforce world/branch/head/role/budget custody in the tool server.
7. Emit retrieval manifests, omissions, and stage timing.

Exit gate:

- one character ID returns complete current role-authorized context;
- hard facts are exact-fetchable;
- unrelated characters and sibling worlds remain inaccessible;
- dossiers rebuild after deletion;
- default turn retrieval usually completes in one tool call.

### Phase 4 — Codex cognition and decision-rationale integration

**Rationale:** sequence-first ordering alone does not prove a meaningful
character decision.

Work:

1. Version the Planner prompt, provider schema, adapter, and session identity.
2. Implement the staged cognition protocol in one retained Planner operation.
3. Add observer frames, evidence expansion, layered mind/body response,
   qualitative pressure comparison, adaptive alternatives, and draft-local
   multi-character reevaluation.
4. Add `DecisionRecordV1` and link material sequence items.
5. Implement critical locked-fact correction and false-dialogue distinction.
6. Implement global autonomy and depth controls.
7. Implement `adult_handoff_required` before visible generation.
8. Add collapsed rationale projections without hidden chain-of-thought.

Exit gate:

- semantic and metamorphic Planner fixtures pass;
- irrelevant context does not alter decisions;
- material counterfactuals do;
- multi-character duplicate interventions are prevented;
- mind/body modes behave according to creator definitions;
- decision bundles remain compact.

### Phase 5 — Ordinary Writer, concurrent Luna/Reader validation, automatic acceptance, and Recorder

**Rationale:** DeepSeek prose needs semantic checking, while Python must not
become a narrative judge.

Work:

1. Build a concise Pi Writer view from selected decisions and context.
2. Preserve Writer freedom over wording, chronology, POV, atmosphere, staging,
   pacing, and compatible interpolation.
3. Add separate Luna Extra High validation with candidate isolation.
4. Adapt the existing narrow Reader contract into a fresh isolated Pi Reader
   stage bound to the same exact candidate without receiving Luna's verdict.
5. Show exact prose provisionally while Luna and Reader run concurrently.
6. Persist independent pending/pass/reject verdict state and reconcile it across
   reload and restart without automatic provider redispatch.
7. Implement hard critical one-repair behavior and retained candidate
   inspection; exclude every D-220 soft-only candidate from that repair path.
8. Implement exact-once automatic Accept after both verdicts and deterministic
   checks, plus an optional per-chat Manual Review mode for the fully passing
   path.
9. Implement D-220 standing-policy evaluation after the independent check join:
   hard wins, Python must pass, all rejected reasons must be exactly allowlisted,
   and a qualifying first candidate is accepted once as provisional continuity
   with immutable policy/audit provenance and zero additional Writer calls.
10. Implement Regenerate as fresh realistic logic-owner judgment and Replan as
   explicit reasoning correction.
11. Keep ordinary Recorder post-Accept, sequence-subordinate, and exact-once.
12. Rebuild affected dossiers and indexes after recording.

Exit gate:

- faithful prose passes;
- omitted/contradicted decision or locked fact fails;
- severe repetition, voice, pacing, depth, readability, or stopping failure is
  independently Reader-rejected;
- compatible variation passes;
- Luna and Reader run independently and concurrent execution is accounting-safe;
- automatic Accept is atomic and idempotent only after both pass;
- Manual Review remains provisional until explicit Accept;
- each exact soft Luna class and permitted Reader scope reaches one V3
  provisional acceptance with the first candidate, zero second Writer calls,
  no creator action, retained rejected evidence, and nonforgeable policy
  provenance;
- mixed soft/hard, Python non-pass, every non-allowlisted Luna class, Reader
  inconclusive, and `whole_candidate` remain hard and cannot be downgraded;
- failed repair remains inspectable;
- provisional acceptance and pinned dependencies work;
- Recorder failure leaves accepted prose intact and repairable.

### Phase 6 — Adult Scene, Filter, projection, and route transitions

**Rationale:** adult logic must remain coherent and reconstructible without
leaking protected content into ordinary roles or duplicating semantic owners.

Work:

1. Implement the protected Adult Scene port with the same functional cognition
   stages and world-local retrieval.
2. Implement a separate Adult Filter/Validator invocation.
3. Stage full protected record, safe projection, and next-route state before
   acceptance.
4. Validate identity, hashes, links, privacy, and transaction shape in Python.
5. Auto-promote all accepted adult artifacts atomically after semantic pass.
6. Remove the redundant post-Accept Adult Recorder.
7. Implement `return_to_codex` for the next prompt.
8. Implement keyword/concept-indexed adult craft retrieval from the
   Genesis-pinned global library.
9. Prove ordinary return, restart, and fork from the safe projection.

Exit gate:

- one candidate has one logic owner;
- rejected adult output has zero accepted effect;
- full record and projection remain synchronized;
- ordinary Codex sees no protected prose;
- DeepSeek can continue from protected continuity;
- route transitions survive restart and fork.

### Phase 7 — SillyTavern controls, review lifecycle, and readable diagnostics

**Rationale:** technically correct backend behavior is not test-ready when Ted
receives generic API errors, invalid review IDs, or invisible state.

Work:

1. Propagate every configured control through HTTP to runtime contracts.
2. Make review/candidate IDs durable, typed, idempotent, and restart-safe.
   Ordinary review V3 is current; V2 remains historical decode only.
3. Make successor generation transactional so failure cannot strand the old
   review.
4. Add logic-owner, validation, recording, and route indicators.
5. Add collapsed decisions, associations, provisional dependencies, removed
   candidates, and debug files.
6. Map backend failures to stable user-readable errors and expose the manual
   transport Retry only with a durable zero-effect proof.
7. Verify installed integration hashes before any authorized synchronization.

Exit gate:

- Accept, provisional acceptance, Decline, Regenerate, Replan, restart, and
  new-chat flows work without stale IDs;
- the browser can display but cannot originate, alter, or replay the output-only
  `standing_policy_accept_provisional` decision or its hash-bound audit;
- an eligible transport failure can be retried once per backend-issued identity
  and explicit click, up to two Retry actions and three total attempts, without
  resuming an interrupted provider thread or duplicating accepted state;
- the third failed attempt shows a critical Codex/Planner error, exposes no
  fourth Retry action, and does not fall back to another provider;
- every failure states whether accepted state changed;
- no generic unexplained `Bad Request` remains for known error classes.

### Phase 8 — Provider-free qualification and source freeze

**Rationale:** live calls should measure the intended architecture, not debug
unfinished interfaces.

Work:

1. Run compile, formatting, lint, type, unit, fault-injection, and focused
   integration gates during implementation.
2. Prove the actual launcher, HTTP, workspace, retrieval, Planner, Writer,
   Validator, Adult Filter, Recorder, review, reducer, and restart call shapes
   through fake transports.
3. Prove that the qualification launcher passes the same five manual-Retry
   seams as the normal lean server: failed-owner reinitializer, provider-ledger
   snapshot, active-thread snapshot, fresh-thread initializer, and completed
   uncommitted Planner abandoner.
4. In the disposable SillyTavern tree, hash-bind and execute the repository
   proxy and creator-extension Node suites. Preflight authenticated Retry GET,
   exact empty POST, successor GET, a second exact empty POST, and the terminal
   no-action `attempts_exhausted` GET through the fake loopback relay.
5. Freeze and exact-validate the complete qualification execution policy, not
   only its Retry subsection. A rehashed change to route order, phase order,
   regenerate authority, adult-evidence custody, fallback, or substitution
   fails closed.
6. Prove that a transient SillyTavern readiness miss keeps the disposable
   process alive until the bounded startup deadline. Durable and console
   failure receipts retain only an allowlisted type, category, and hash; raw
   Retry identities, paths, response content, and adult prose are excluded.
7. Route disposable SillyTavern stdout and stderr to the operating-system null
   sink. SillyTavern debug output may contain complete retained requests and
   responses, so no ordinary qualification log file is an acceptable custody
   location for those streams.
8. Make the fake exhausted POST emit the exact full production
   `cera.error.v1` envelope, then prove the staged proxy accepts it, removes
   trace/path/provider prose, and returns only the exact closed terminal
   projection. Reduced or partially shaped direct envelopes fail closed.
9. Run one complete clean-checkout suite after bytes stop changing.
10. Exercise every D-220 soft class/scope, mixed hard-plus-soft precedence,
    Python non-pass, Reader inconclusive/`whole_candidate`, exact-once commit,
    crash/restart recovery, retained verdict evidence, policy/audit tamper,
    zero second Writer calls, and unchanged Adult disposition through the
    actual backend and generated V3 browser contracts.
11. Freeze exact source, prompts, schemas, tools, profiles, fixtures, and
   provider ledgers.

Exit gate:

- clean exact commit/tree;
- complete provider-free suite green;
- zero provider dispatches;
- no installed-user or production mutation;
- one concise readiness result.

### Phase 9 — Bounded live component qualification

**Rationale:** isolate provider and contract defects before blaming the full
story route.

Order:

1. Codex cognition/DecisionRecord fixtures.
2. Pi ordinary Writer fixtures.
3. Luna semantic validation fixtures.
4. ordinary complete route.
5. Adult Scene fixtures.
6. Adult Filter and dual-record fixtures.
7. adult complete route.
8. ordinary/adult transition and restart/fork retrieval.

Use frozen inputs and exact accounting. Fix deterministic backend defects
provider-free. Treat isolated stochastic Writer variation as model variation
unless a generalized cause recurs across varied fixtures. D-220 soft ordinary
variation must exercise standing-policy provisional continuity on the first
candidate; qualification must not spend a second Writer call to make a soft
candidate satisfy the validators.

Exit gate:

- every role meets its contract;
- no hidden retry, fallback, duplicate dispatch, or accounting ambiguity;
- repeated-session latency telemetry is complete.

### Phase 10 — Twenty-turn backend reliability campaign

Run one progressing direct-backend branch first with:

```text
10 ordinary user prompts
10 adult user prompts
```

Preserve every first response, any explicit transport-Retry chain, provider
submission/charge parity, call and thread hashes, terminal states, durations,
and completion hash. If no natural eligible Planner transport failure occurs,
the required Retry action and chain counts are both zero.

It must exercise:

- automatic acceptance;
- at least one relevant multi-character decision;
- route transition and logic-owner indicator;
- adult full record and safe projection;
- return to Codex;
- repeated retained-thread latency;
- exact provider accounting;
- no automatic Retry or fallback.

Exit gate:

- all twenty prompts progress without API, persistence, recording, session, or
  route error;
- every charged failed Planner attempt, if any, is additive to the successful
  replacement call rather than hidden by HTTP projection accounting;
- protected information does not leak.

### Phase 11 — Ten-turn isolated SillyTavern creator-readiness campaign

Only after Phase 10 passes, run the same frozen route through the hash-bound
disposable SillyTavern copy with:

```text
5 ordinary user prompts
5 adult user prompts
```

It must exercise:

- automatic acceptance and the same route boundaries as the backend run;
- authenticated local relay and exact completion metadata;
- one process restart;
- one fork;
- ordinary retrieval of the safe adult projection;
- no cross-chat retrieval by a separate new chat.

The pass is technical. Ted remains the judge of subjective adult prose.

Exit gate:

- all ten prompts progress without API, review-ID, persistence, recording,
  retrieval, session, or route error;
- accepted state is coherent after restart and fork;
- installed SillyTavern and its user data remain untouched;
- protected information does not leak.

The combined backend and SillyTavern campaigns expose common technical,
critical, major, and generalized defects without demanding perfect stochastic
prose. Freeze the exact source, route, prompts, tools, schemas, profiles,
fixtures, and provider ceilings before either campaign, and preserve every
first-pass result before any user action.

Measure:

- API and transport reliability;
- review and acceptance reliability;
- decision-rationale quality;
- character knowledge and autonomy;
- route ownership and transitions;
- branch/session isolation;
- Recorder and dossier consistency;
- Regenerate/Replan behavior;
- restart and fork recovery;
- adult projection continuity;
- latency, tokens, cache, tool calls, and provider accounting;
- first-pass model variation separately from generalized backend defects.

An isolated Writer miss is not automatically a source defect. A repeated
mechanically identical failure or a generalized architecture failure is.

Exit gate:

- one concise campaign result;
- no common crash, API, review, persistence, branch, route, or recovery defect;
- all accepted state reconstructs correctly;
- no automatic production promotion.

### Phase 12 — Creator testing and later promotion decision

Ted receives:

- exact launch/profile instructions;
- current limitations;
- rollback and recovery steps;
- debug-log location;
- model/route identities;
- measured latency and reliability;
- protected-data handling summary;
- exact source commit/tree.

Creator testing may produce product-quality changes. Production promotion,
deployment, LAN/public exposure, or broad installed-user mutation remains a
separate decision.

## 14. Semantic and metamorphic test program

### 14.1 Cognition tests

- Hold the event fixed and change trusted speaker versus stranger.
- Change public versus private audience.
- Change direct observation versus hearsay.
- Change relationship evidence while preserving unrelated facts.
- Change active protective goal versus no relevant goal.
- Compare `off / mind / body / both`.
- Compare impulsive and reflective characters under the same event.
- Verify a false narrative fact is corrected while false dialogue remains a
  claim.
- Verify one dominant decision remains stable across Regenerate.
- Verify another close realistic choice can emerge without character drift.

### 14.2 Invariance tests

- Reorder unrelated memories.
- Add an absent character biography.
- Rename internal custody IDs.
- Paraphrase nonmaterial narration.
- Reset provider sessions and caches.

Material decisions should remain semantically stable.

### 14.3 Multi-character tests

- Two characters notice the same event; only the actor with the strongest
  immediate reason takes the floor first.
- Later actors reevaluate after the first action and do not duplicate it.
- Identity-specific goals remain open after another character completes a
  superficially similar action.
- Background and referenced characters do not become responders automatically.

### 14.4 Memory and development tests

- A trivial phrase creates no durable record.
- A material public insult creates an event and possible owner-specific memory
  without immediately creating a permanent trait.
- A similar later cue may activate subconscious pressure without granting new
  knowledge.
- Apology and counterevidence change current appraisal without deleting the
  historical event.
- Repetition can habituate, sensitize, or improve expression masking.
- Stable trait promotion requires review.
- Old accepted state remains retrievable beyond the recent prose window.

### 14.5 Validation tests

- Luna accepts materially faithful variation.
- Luna rejects missing intent, wrong outcome, false locked fact, invented Ted
  dialogue/private state, knowledge leak, and unauthorized consequential E.
- Luna does not reject harmless staging or prose style.
- D-220 classifies only `omitted_decision`, `presence_violation`,
  `stopping_boundary`, and `capability_restriction` as soft Luna rejection
  classes for ordinary provisional continuity; the validator verdict itself is
  not rewritten.
- A Reader rejection is D-220 soft only when every issue scope is
  `exact_quote` or `omitted_planner_item`; Reader inconclusive and any
  `whole_candidate` issue are hard.
- Python non-pass or any hard Luna/Reader signal defeats every simultaneous
  soft signal. Policy or audit hash drift fails closed.
- Adult Filter preserves full/safe event binding and route state.
- Failed validation cannot alter accepted state.
- Critical repair retains the removed candidate for Ted.
- Standing-policy provisional acceptance uses the first candidate, makes zero
  second Writer calls, needs no creator action, retains rejected evidence, and
  marks every dependent scene.

### 14.6 Transaction and recovery tests

- Crash at every Accept and recording publication boundary.
- Duplicate decision replay is idempotent.
- Regenerate/Replan successor failure leaves the old review usable.
- Restart restores provisional and accepted identities without provider calls.
- Fork never carries rejected candidates or live cross-branch provider state.
- Dossier and indexes rebuild from accepted normalized records.
- Provider accounting remains cumulative across restart.

## 15. Performance and prompt telemetry

### 15.1 Latency target

For an existing retained Codex session, the ordinary logic path target is under
three minutes under the chosen quality configuration. Testing must not
lower effort, simplify the cognition protocol, or alter model settings merely
to satisfy that target.

The first Planner operation on each physical provider thread is a separate
cold class. The first thread in a new chat/session is a cold start; a replacement
thread created after a proven transport failure is a cold rehydration. Both are
expected to be longer because they establish retained provider context and a
world/evidence cache. They must be reported separately and must not be used to
classify later retained-thread performance. A backend-process restart that
resumes the same physical thread remains retained.

A second or later retained Planner operation at or above 180 seconds is a
latency concern. CERA records the owning stage, retrieval activity, cache/token
telemetry, and duration for optimization, but does not automatically cancel or
retry it. The summary records the retained-call sample count, maximum, and
average using only `cold_start=false` observations; cold start and cold
rehydration samples never enter that average, including across a backend
process restart that retains the same physical thread. The provider hard
transport-loss boundary is 600 seconds. The outer
qualification HTTP timeout is 4,200 seconds: six sequential 600-second provider
stages plus one stage of margin, so the client cannot abandon backend work that
may still commit. If either explicit Retry POST becomes ambiguous, read-only
GET reconciliation for that exact identity remains available for 4,215 seconds
and never repeats the POST. Retry timing uses wall-clock duration for each POST
even when its response is lost, and for the complete GET reconciliation window
including poll intervals; it must not report either ambiguous wait as zero.

When a change materially increases latency, record the increase and its owning
stage before optimizing.

### 15.2 Required stage timing

Record:

- packet-ready time;
- provider request start/end;
- each retrieval tool start/end and result bytes;
- final evidence time;
- first reasoning and first structured-output times when available;
- Python parse and validation time;
- Writer, Luna, Adult Scene, Filter, Recorder, and persistence durations;
- rehydration versus retained-session status;
- per-step and cumulative input, cached input, uncached input, output, and
  reasoning tokens;
- tool-call count and provider-operation count;
- prompt/schema/tool versions.

### 15.3 Efficiency acceptance criteria

- Most ordinary turns use one default context tool plus only justified
  expansions.
- Adding the cognition contract does not require a second Planner provider
  call.
- Material DecisionRecords remain substantially smaller than hidden exhaustive
  appraisals.
- Stable prompt and tool prefixes achieve useful cache reuse when the provider
  supports it.
- A cache miss affects time, not correctness.
- No optimization removes relevant accepted continuity or observer knowledge.

## 16. Critical-change policy during implementation

Codex may adjust this roadmap when a change is necessary to preserve
correctness, maintainability, privacy, branch authority, reconstructibility, or
realistic rational decision-making.

A non-creator-blocking adjustment is allowed only when it:

1. preserves the product goal and one-logic-owner rule;
2. preserves or improves character rationality;
3. does not weaken accepted-state, privacy, branch, or provider-accounting
   boundaries;
4. does not add an unrequested semantic restriction;
5. has a concise written rationale and rejected alternative;
6. has focused proof before it becomes the new checkpoint.

Codex must stop for Ted when an adjustment changes:

- who owns a semantic decision;
- what can become canon;
- creator-locked facts or world semantics;
- autonomy meaning;
- ordinary/adult route meaning;
- what protected content a role may receive;
- automatic acceptance or provisional-canon behavior;
- visible creator controls;
- provider ceilings, production effects, or deployment.

Prompt growth is not the default repair. Before adding a rule, classify the
failure as:

```text
transport
custody/transaction
missing or stale context
retrieval/tool contract
logic-owner decision
Writer stochastic variation
semantic Validator issue
UI/review lifecycle
test-fixture drift
```

Change architecture only for an evidence-supported generalized cause.

## 17. Deferred or excluded decisions

The following are not silently resolved by this roadmap:

- final adult eligibility policy previously deferred by Ted;
- production deployment or public/LAN exposure;
- final provider promotion beyond the initial qualified adapters;
- whether later evidence justifies a different Adult Filter model/mode;
- automatic stable-trait promotion, which remains disabled pending explicit
  review design;
- wholesale migration or deletion of historical CERA/Vera evidence;
- unrestricted runtime model access to arbitrary files, repositories, drives,
  chats, or provider histories.

## 18. Final completion definition

CERA reaches initial full-model creator readiness only when:

- every new chat has an isolated Genesis-backed world;
- every fork inherits the exact selected accepted state and diverges safely;
- one character ID returns complete current role-authorized context;
- Codex performs the staged cognition protocol for ordinary decisions;
- DeepSeek performs the equivalent adult logic role on adult turns;
- every material decision has a concise evidence-linked record;
- mind/body autonomy behaves according to the global setting;
- locked factual contradictions are corrected rather than canonized;
- ordinary prose is independently Luna- and Reader-checked, with pass or exact
  D-220 soft-only standing-policy provisional disposition recorded in V3;
- adult prose is independently Filter-validated;
- accepted prose, decisions, records, and projections commit atomically or
  enter an explicit repairable state;
- hard-rejected and unresolved candidates never contaminate accepted retrieval;
- provisional dependencies remain pinned and traceable;
- route transitions preserve one logic owner per candidate;
- prompt context is compact, tool-driven, and branch-confined;
- retained-session turns meet or explain variance from the three-minute target;
- the provider-free suite, six-message SillyTavern smoke, and twenty-turn
  reliability campaign pass their technical goals;
- Ted can inspect decisions, associations, validation, and failures through
  collapsible SillyTavern views;
- no common API, review-ID, persistence, route, restart, fork, or cross-chat
  failure blocks normal testing.

The roadmap's final design rule is:

```text
CERA should not imitate thought by adding more rules.
It should give one capable logic owner the right evidence,
require a concise auditable decision,
let a separate Writer realize it naturally,
and preserve only accepted consequences with exact branch authority.
```
