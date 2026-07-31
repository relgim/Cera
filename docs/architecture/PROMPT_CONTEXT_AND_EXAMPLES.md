# Prompt, Context, and Example Selection

**Status:** controlling packet-construction contract

## 1. Packet layers

Every model packet is assembled in this order:

1. role contract;
2. hard global boundaries;
3. current branch/source authority;
4. validated evidence and unknowns;
5. selected role-specific context;
6. selected craft modules/examples;
7. final current-turn authority block.

Later packet position does not change data authority. Python labels each block by class and validates that no lower class contradicts a higher one.

## 2. Reasoner packet

Runtime Codex receives:

- non-graphic exact source or semantic abstraction;
- typed seed dossier and evidence handles;
- active and eligible cast;
- per-character evidence partitions;
- scene/material/relationship state;
- hard adult, consent/capacity, branch, privacy, and Ted boundaries;
- schema and tool definitions.

The active provider schema is `CodexReasonerDraftV3`, not
`ReasonerOutcome`. Codex provides semantic route, participant/floor choices,
character moves, local beat/segment keys, evidence IDs, uncertainty, and
beat-local craft requirements. It does not provide canonical IDs, record
versions, hard-citation objects, hashes, normalized labels, or route
bookkeeping. Python derives those fields after exact-evidence validation.

The Codex transport receives a versioned OpenAI-compatible projection of this
same DTO. Provider syntax limitations never change semantic field ownership:
the projection may replace the disjoint AdultCraftNeed channel-owner `oneOf`
with `anyOf`, but dialogue/inner voice still require a character owner, scene
channels still forbid one, and Python decodes and validates the original
domain model after return.

The v4 Reasoner prompt explicitly teaches the three status-dependent field
matrices and identifies every semantic-set array that must contain distinct
items. These are guidance, not authority: Python reports safe field-path codes
and rejects invalid combinations or duplicates after return.

It does not receive Adult ON/EX prose. For a consent-valid adult decision, it emits only typed `AdultCraftNeedV3` semantics derived from validated current beats. Each channel separately declares `semantic_concepts` and `lexical_concepts`: semantic concepts select technique and semantic verification but create no word quota, while lexical concepts are an explicit subset and alone create deterministic vocabulary requirements. The beat needs are a non-empty subset containing each current beat that actually requires adult-specific craft; ordinary pauses or transitions need not be duplicated. Every cited beat ID must still belong to the current segment. The need contains no craft excerpt or realized adult prose.

Semantic abstraction may remove unnecessary explicit vocabulary but must preserve:

- actor, target, deliberateness, and sequence;
- requested/attempted/ongoing/completed status;
- actual consent, capacity, pressure, refusal, withdrawal, and discomfort;
- what each person perceives, knows, believes, or does not know;
- material/body state required for continuity;
- outcome and uncertainty.

It must never convert deliberate to accidental, non-consent to ambiguity, discomfort to desire, or an event to a harmless analogy.

## 3. Character realization cards

Select cards only after participant/floor selection. The Phase 15 whole-`speech_system` default remains available for non-adult compatibility, but the Adult ON/EX path requests only named sections for selected characters. Baseline voice is retained with each requested section. A Composer card can contain current-useful:

- voice and dialogue tendencies;
- outward/inner/action distinctions;
- present goals and pressures;
- relevant relationship stance;
- relevant body/material continuity;
- specific knowledge and privacy boundaries;
- current branch development.

It excludes unrelated biography, inactive-character cards, unsupported predictions, fixed trajectories, and example dialogue that could become a script.

### 3A. Hanezawa V1.2 expression evidence

V1.2 does not add a second model-owned style manifest. When body, dignity,
refusal, family defense, objectification, betrayal, or trust fracture is
material, runtime Codex selects the semantic response through its ordinary
character move/beat and cites only the relevant exact
`character_expression` evidence. Python validates the citation, ownership,
knowledge, selected cast, and Genesis revision, then passes the exact bounded
sections through `ComposerContextKind.CHARACTER_EXPRESSION`.

The ordinary useful packet is:

```text
existing speech_system baseline
+ zero or one embodied/signature reference
+ zero or one selected response-mode reference
```

A response-mode reference may expose two creator examples only when Codex
fetched that exact mode. The examples are labeled non-executable and
noncopyable; they cannot establish past dialogue, future conduct, Ted's motive,
consent, trust change, or durable truth. Unrelated ordinary scenes receive no
expression block, and unselected characters receive none.

## 4. Adult example selection

Examples are cataloged by:

- example ID and version;
- consumer role;
- content-family tags;
- compact/full mode;
- technique taught;
- activation requirements;
- prohibited inferences;
- source hash and provenance;
- evaluation partition.

Selection occurs only when a validated `AdultCraftNeed` activates a consent-valid adult content family. Python performs deterministic set-cover selection against required beat/channel concepts and a mode-specific byte budget. There is no fixed one-or-two-module quota: the smallest sufficient set may contain any count within budget, and incomplete coverage fails closed.

ON is compact/direct and appropriate when the plan needs vocabulary and limited craft support. EX is the default for active explicit scenes unless the semantic need proves ON sufficient. Anal and toilet/scat remain separate families even when one scene activates both.

Examples may guide buildup, causal physical continuity, sound placement, threshold emphasis, voice/interiority/physical/material layers, and aftermath. They cannot establish a current act, preference, experience, consent, climax, injury, pregnancy, relationship fact, or character identity.

No example is selected for a blocked non-consensual event. Coverage declarations are audit claims only; direct/vulgar specificity requires an actual compiled vocabulary group for the selected concept.

## 5. Adult Mechanics packet

When active, it receives:

- validated non-graphic SequencePlan;
- synchronized safe causal ledger (never exact protected prose);
- actual consent/capacity scope;
- active-character realization cards;
- selected hash-bound CERA craft fragments from the authorized Adult ON/EX catalog;
- material/position continuity;
- content-family and stop boundaries;
- prohibited additions.

Its output is an advisory `AdultMechanicsProposal`, not psychology, prose, or truth. Python verifies source-unit, participant, progression, craft, decision, and no-psychology-override bindings before the Composer sees its hash-bound result.

## 6. Composer packet

The Composer receives only what it needs to realize the accepted decision. It never receives:

- database credentials or retrieval tools;
- unfiltered master indexes;
- other characters' irrelevant private memory;
- rejected candidates or temporary projections;
- the entire example library;
- unresolved instructions that could override the current-turn authority.

The active provider DTO is deliberately minimal. DeepSeek returns ordered,
local-keyed presentation-neutral story segments, segment references for
creator-source coverage and beat/participant realization, and one terminal
segment key. It
does not return a `SemanticInference` list, participant-realization booleans,
floor/cast hashes, generated IDs, validation flags, or a duplicate decision
manifest. Python joins segments with a fixed double-newline policy and derives
those records from the validated decision and exact segment spans. Unknown
fields, unknown segment keys, malformed enums, missing participants, or
incomplete beat/source references fail after one call.

Because DeepSeek uses JSON-object mode instead of OpenAI's strict-schema
dialect, its packet includes a versioned portable prompt-schema projection.
That projection teaches the required shape; it does not replace Python typed
decoding, segment resolution, semantic validation, or independent realization
checking.

The context assembler operationalizes this boundary. It reuses exact records that supported validated selected-character moves. For Adult ON/EX it extracts only the `speech_system` sections requested by `AdultCraftNeed`, plus baseline voice, for selected NPCs. For Hanezawa V1.2 it classifies cited expression evidence separately and preserves its applicable speaker. A versioned context-assembly receipt stores only hashes, evidence/craft IDs, and lookup counters. Exact context remains transient. Missing or ambiguous selected-character voice evidence is a pre-provider error, not permission for DeepSeek to infer a generic personality.

The v6 Codex Reasoner prompt tells Codex to retrieve expression evidence only
when semantically relevant and to cite it on the owning move or beat. The v6
DeepSeek Composer prompt is hash-bound to
`character_specific_speech_realization_v1_2.txt`; runtime code carries the
rules and source hash without reading that filesystem path. DeepSeek preserves
the selected intent/boundary and creates fresh syntax, moral reasoning, and
natural comparison. It does not choose the response mode or copy Genesis
phrasing.

The adult Composer packet carries a channel-aware `SpecificityContract`. Narration, dialogue per character, inner voice per character, sound effects, and physiology are distinct channels. Each required term/concept is bound to a specific current beat and channel. Climax and aftermath each carry two independent axes: authority (`required|allowed|prohibited|unresolved`) and current-segment commitment (`selected|conditional|not_selected`). Craft cannot promote an unresolved or merely allowed outcome into a selected event.

After composition, Python first validates vocabulary specificity inside the declared beat/channel spans. A provider-neutral semantic-specificity verifier then receives only those locked beat spans, the contract, safe selected IDs, actor/action/object/material bindings, and required transitions. It may reject ambiguity despite token presence, mismatched associations, missing/reversed transitions, contradicted outcomes, or false coverage declarations. Its privacy-safe receipt retains no prose.

If exactly one beat fails either verifier, a separately bound one-attempt repair may replace only that span. Python then reruns deterministic specificity, runs a fresh no-retry semantic verification, and finally reruns full structural validation. A post-repair semantic failure is terminal and cannot trigger another repair. This is a provider-free contract path, not a promoted live repair policy.

After structural composition validation, an independent
`SceneRealizationVerifierPort` must accept the complete expected beat sequence
and selected participant set plus the Python-required protected-user semantic
boundary. Composer anchors are input evidence, not self-validating proof. A
protected-user rejection requires an anchored, text-hash-validated finding;
durable receipts keep only safe codes and hashes. Only a verifier receipt can
authorize the candidate to cross the objective-event/publication boundary.
Scripted/echo fakes are not qualification-eligible.

The Sol-medium verifier packet contains only the complete candidate, semantic
expected beats and their required states, selected participant IDs, exact
protected-user authorities and allowed source kinds, required hard boundaries,
and prevalidated Composer anchors. It receives no Genesis tools, retrieval
bridge, filesystem access, database access, Writer instructions, or craft
examples. Its system and request instructions explicitly forbid continuation,
rewriting, repair, stylistic improvement, replacement prose, or invention. It
returns a minimal structured semantic draft. When it reports a violation, it
must quote one exact unique span from the supplied candidate; Python owns quote
resolution, occurrence, offsets, hashing, cross-field validation, and final
gate status. Unavailability or invalid output ends the turn without retry or
fallback.

## 7. Context pressure

When a packet exceeds budget, retain in this order:

1. current source and hard boundaries;
2. validated decision/SequencePlan;
3. actual consent/capacity and protected-user rules;
4. active scene/material/knowledge state;
5. evidence cited by the decision;
6. active-character realization cards;
7. selected compact craft;
8. optional examples and background.

Dropping required authority is a route error, not a license to guess.

## 8. Inactive modular Composer compiler seam

D-159 adds a provider-neutral compiler seam without changing the active
Composer route. Its target assembly order is stable authority, structured
output teaching, exactly one Depth profile, exactly one Adult rendering
profile, exactly one topology profile, exactly one scene-function profile,
selected character/evidence material, selected craft, and request-specific
obligations/schema.

Python owns registry lookup, repository-local path validation, module hashes,
exact Depth and Adult rendering modes, cast and eligibility, deterministic
ordering, and the final selected-module receipt. A Reasoner may propose bounded
`scene_function`, `tone`, topology, and interiority values; it may not return a
path, filename, raw prompt, or module ID. DeepSeek receives one compiled packet
and cannot select modules or change the plan.

`AdultRenderingMode` is `off|on|ex` and is independent from consent, route
eligibility, participation, act selection, causality, and `SceneDepthMode`.
OFF rejects all adult craft; ON and EX require an ordered result from the
existing coverage-based Adult selector. The bridge preserves every selected
fragment ID, version, hash, provenance, reason, order, and noncanonical/
noncopyable marker. It imposes no fixed fragment or example count.

Prompt bytes, estimated tokens, module counts, and fragment/example counts are
receipt telemetry. There is no creative token ceiling, quota, automatic
truncation, or silent downgrade. Separate repository-safety bounds prevent a
malformed manifest from causing an accidental unbounded read; they do not
select writing content.

Only fixture and shadow states exist. Final module prose remains un-authored,
and no repository-local active `MANIFEST.json` is installed under the Composer
module directory.
