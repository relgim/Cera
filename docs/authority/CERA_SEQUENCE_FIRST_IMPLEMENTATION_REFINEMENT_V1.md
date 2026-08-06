# CERA Sequence-First Implementation Refinement V1

**Status:** controlling implementation clarification  
**Version:** `cera.sequence_first_implementation_refinement.v1`  
**Date:** 2026-08-05  
**Owner:** Ted  
**Manager:** ChatGPT Pro repository-review thread

## 1. Purpose

This document refines `CERA_SEQUENCE_FIRST_RUNTIME_V1.md` before provider adapters or live qualification. It incorporates the independent Claude reviews of commit `be81cbb454e1edd219fd03cf3c0924bc716a85c2` and the manager review of the newer additive `src/cera/sequence_first` draft.

The old exhaustive and compact-span routes remain readable historical evidence. They are not the new live route.

## 2. Correction order

```text
provider-free implementation correction
-> focused tests and fake pipeline
-> one complete suite at source freeze
-> exact Git review publication
-> independent Claude review of that exact commit
-> manager classification
-> only then a bounded live canary
```

No provider call, SillyTavern qualification, or twenty-turn campaign may begin before the independent review is classified by a newer manager queue.

## 3. Model semantics and Python custody are separate

Provider-facing Planner and Validator outputs contain semantic story data only. They must not author or echo:

- request, candidate, world, branch, scene, turn, parent, session, or provider-thread identities;
- hashes, receipts, revisions, paths, transaction identities, or call-accounting fields;
- model-calculated offsets or persistence operations.

Python privately binds a semantic result to one immutable custody envelope containing those identities and receipts after decoding. Python may reject malformed structure or unknown IDs, but it must not infer story meaning.

Character IDs, evidence keys, protected-source claim keys, Planner item keys, and approved persistence target keys may remain model-visible because they are semantic references with closed Python-owned lookup tables.

## 4. Presence and responder authority

### 4.1 Accepted presence

Python may preserve only the current accepted presence set from the prior accepted realized sequence or scene initialization. It must not infer presence from:

- names or aliases in the current message;
- regex matching;
- references in prior prose;
- prior responding characters;
- retrieval results, summary delivery, or mention frequency;
- a previous active-cast or scene-character list without accepted presence authority.

The exact current creator message is sent to the Planner. If it establishes entry, exit, remote participation, or a scene transition, the Planner represents that meaning and the Validator confirms it.

### 4.2 Responders and background characters

The Planner selects responders by assigning NPC owners to intended sequence items. There is no separate model-authored `responding_character_ids` or `backgrounded_character_ids` field.

Python may derive for mechanical observability only:

```text
responding NPCs = NPC owner IDs on intended sequence items
backgrounded NPCs = accepted present NPCs - responding NPCs
```

This set derivation does not choose narrative salience; Codex already made that choice through item ownership.

An absent character may own a current item only when the semantic item explicitly represents authorized remote/off-screen participation or an ordered presence entry supported by accepted authority or exact current creator source.

### 4.3 Presence changes

Presence changes are ordered semantic output, not Python prose inference:

```json
{
  "character_id": "character:mia_hanezawa",
  "direction": "enter|leave",
  "effective_after_item_key": "item_key"
}
```

Python mechanically applies Validator-accepted presence changes to the prior accepted set. Ambiguous prose causes no guessed presence update.

At a scene change, the first accepted sequence of the new scene re-establishes presence; it is not inherited mechanically from the prior scene.

## 5. Minimum intended and realized sequence

A provider-facing sequence contains only future-relevant semantic meaning:

- ordered items;
- one primary owner ID when the item requires an owner;
- one small item kind;
- concise meaning;
- optional causal parent item key;
- evidence keys;
- exact protected-source claim key when the item asserts supplied Ted content;
- referenced durable-change keys;
- ordered presence changes;
- resulting public state;
- unresolved threads;
- stopping boundary.

Do not add affected, observing, addressed, referenced, or other role arrays unless a concrete Python mechanical operation cannot be performed without them. Harmless visible interaction does not need a durable role record.

A realized item carries zero or more exact Planner item keys. Beat coverage is derived as the union of those references. There is no separate model-authored coverage field.

A compatible significant Writer addition is a realized item with no Planner item reference. Harmless additions remain only in visible prose and are omitted.

## 6. Durable changes and persistence targets

A durable change contains:

- a small change kind;
- subject IDs;
- concise semantic change;
- visibility and private owner when needed;
- one approved opaque `target_key`.

The turn packet exposes only target keys authorized for the current branch and turn. Python privately maps each key to an exact record, revision, field policy, and transaction operation. The model never returns file paths, record revisions, JSON patches, or persistence directives.

The existing atomic transaction and optimistic-revision machinery should be reused rather than replaced.

## 7. Validator decision

The Validator decision is binary:

```text
verdict = accept | reject
```

An accepted decision may also carry orthogonal `review_flags` and optional severity for creator attention. These do not form a third semantic verdict.

On accept, return:

- one concise realized sequence;
- durable changes and presence changes contained by that sequence;
- resulting state and unresolved threads;
- optional review flags.

On reject, return only:

- a small closed conflict class;
- a concise explanation;
- the shortest exact quote from frozen Writer prose or one omitted Planner item key.

Python verifies quote membership, key existence, IDs, schema, and branch custody. It does not reinterpret the conflict.

Writer recall eligibility is Python-derived from the conflict class and controller failure type. It is not a model-authored Boolean. Ordinary semantic Writer conflicts may open the next fresh attempt. Transport, schema, branch, authority, accounting, or capability failures do not.

## 8. Provider session model

### Planner

- one persistent branch-bound Codex Planner thread;
- stable instructions supplied once as base instructions;
- ordinary continuation sends only the exact current message, prior accepted realized sequence/current state delta, new character or evidence deltas, protected-source claims, unresolved threads, and hard boundaries;
- no automatic full-record retrieval for every present or previously active character.

### Validator

- one fresh candidate-specific Codex Validator thread;
- compact stable instructions supplied as base instructions;
- one compact semantic request;
- archive and prove non-resumability after the result;
- no accumulated prior candidate context and no exhaustive default fallback.

Every live call site must explicitly select the sequence-first profile. A silent default to `exhaustive_v13` is prohibited.

### DeepSeek Writer

Preserve the proven minimal response wire:

```json
{
  "schema_version": "...",
  "story_text": "..."
}
```

DeepSeek writes prose only. Preserve the maximum-three fresh attempts and no-merge rule.

## 9. SillyTavern integration boundary

The new Stage 6 path must not use Python regexes, aliases, current-message names, prior active-cast lists, or fallback characters to choose presence or responder eligibility.

Any retained `TurnKernel`, ingress, or seed-dossier component must be proven provider-free and mechanical. Its output may carry exact source, accepted custody, branch, record revision, and access scope. It may not choose:

- present characters;
- eligible responders;
- requested Planner characters;
- narrative salience;
- psychology or causal sequence.

The Stage 6 test must deliberately mention an absent Mia without causing her to become present or responsive, and must keep a present subtle Mia silent when the Planner gives her no owned item.

## 10. Deferred capability-restricted route

Do not implement or activate the adult capability route during this correction. Preserve its documents.

Before future implementation, benign tests must compare:

```text
Codex boundary sequence -> DeepSeek Writer -> Safe Formatter
```

against:

```text
Codex boundary sequence -> DeepSeek Planner -> DeepSeek Writer -> Safe Formatter
```

The DeepSeek Planner remains provisional until evidence shows it materially improves required-event and character-logic coverage enough to justify an additional inconsistent semantic stage.

## 11. Historical compatibility

Historical rich, exhaustive, and compact-span schemas remain readable and immutable for evidence replay. The new live route is additive. Do not force new turns through historical DTOs, defaults, prompts, or persistence projections merely because old tests depend on them.
