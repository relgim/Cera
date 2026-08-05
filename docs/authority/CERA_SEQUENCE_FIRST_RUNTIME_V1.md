# CERA Sequence-First Runtime V1

**Status:** controlling creator architecture decision  
**Version:** `cera.sequence_first_runtime.v1`  
**Date:** 2026-08-05  
**Owner:** Ted  
**Manager:** ChatGPT Pro repository-review thread

## 1. Decision

CERA will use one sequence-first semantic contract. Codex owns story logic. DeepSeek owns visible prose. Python owns mechanical custody and transactions only.

```text
Creator prompt
-> Python freezes exact bytes and retrieves accepted state without semantic inference
-> persistent Codex Planner creates an intended sequence
-> stateless DeepSeek Writer realizes that sequence as prose
-> independent Codex Validator compares prose with the intended sequence and prior accepted state
-> Validator returns the realized sequence or concise material conflicts
-> independent Reader judges visible quality
-> Python atomically stores the accepted realized sequence and deterministic receipts
```

Python must not decide character salience, select responders, infer who is active, interpret prose, rank additions by narrative importance, or repair model meaning.

## 2. Presence is not activity

CERA must distinguish factual scene presence from current narrative activity.

- `present_character_ids` may exist only when presence is explicit in accepted story state or creator source.
- Python may preserve that accepted fact but may not infer presence from recency, mention count, retrieval score, or metadata.
- Python must not emit `active_cast_ids` as semantic authority.
- The Codex Planner chooses `responding_character_ids`, the conversational floor, who remains backgrounded, and whether no NPC should act.
- A present but subtle character such as Mia must not become more salient merely because Python supplied or retrieved her record.
- The Planner may select a responder absent from the preceding turn only when accepted scene state makes that character present or eligible and the choice is causally justified.

On a persistent Planner thread, first appearance or scene initialization may deliver relevant character material. Ordinary continuation turns deliver only accepted sequence/state deltas. Full character records are not automatically reread. Retrieval occurs only for a specific Planner-declared evidence need, a newly present character, a changed record revision, or a scene reconstruction after thread loss.

## 3. Shared sequence model

Planner intent and Validator realization use the same conceptual sequence family.

A sequence contains only information required for causal logic or future continuity:

- ordered material beats;
- the NPC owner or speaker for each beat;
- concise action, dialogue-intent, or private-state meaning;
- causal relation to the prior beat;
- significant material, knowledge, or relationship changes;
- resulting public scene state;
- unresolved threads;
- the protected-user stopping boundary;
- factual presence changes when they materially occur.

A sequence does not contain:

- every visible gesture, sentence, object, or atmospheric detail;
- gap-free prose partitions;
- seven-role ledgers for every span;
- model-calculated hashes or offsets;
- duplicate field, item, event, and persistence representations;
- Python-selected active responders;
- harmless details that future turns do not need.

The accepted realized sequence is deliberately lossy. Visible prose remains in the transcript, but only material causal meaning enters durable authority.

## 4. Planner input and output

### Planner input

An ordinary continuation turn receives:

- the exact current creator message;
- the prior accepted realized sequence or the compact current-scene sequence state;
- new accepted durable changes since the last Planner turn;
- factual present/eligible character information only when explicitly established;
- compact relevant character deltas when changed or newly needed;
- exact protected-user source supplied on the current turn;
- the current stopping boundary and unresolved threads.

Python-held hashes, branch receipts, record paths, ancestry receipts, synchronization receipts, provider-thread identities, and transaction records remain outside model-visible context unless an opaque identity is strictly required by the provider schema.

### Planner output

The Planner chooses:

- current responders and backgrounded characters;
- ordered causal beats;
- character motive and private-state direction;
- significant physical or material continuity;
- open realization freedom for DeepSeek;
- the point where the response returns control to Ted.

Python validates only structure, known IDs, exact creator-source references, and mechanical limits. It does not judge whether the selected responder or psychology is correct.

## 5. DeepSeek Writer

The Writer receives:

- the intended sequence;
- compact voice and relevant character cues;
- the current public scene anchor;
- exact creator source where needed;
- hard boundaries and stopping point.

It writes natural prose. It may add harmless presentation detail. It does not return semantic metadata, memory records, active-cast decisions, hashes, offsets, or acceptance claims.

Up to three independent Writer attempts are allowed for one frozen package. Outputs are never merged. Recall feedback contains only concise material conflicts or quality failures.

## 6. Codex Validator

The Validator reads the entire exact Writer response, but it does not formally classify every character of it.

It compares:

1. the intended Planner sequence;
2. the Writer prose;
3. the prior accepted realized sequence/current scene state;
4. exact current creator source and protected-user boundary.

It asks only:

- Were the required causal beats materially realized in a compatible order?
- Did the Writer contradict accepted state, character logic, knowledge, presence, or the stopping boundary?
- Did it invent Ted action, dialogue, private state, consent, choice, response, or relationally implied participation?
- Did it add a consequential event, relationship change, knowledge change, material change, or presence change large enough to affect future turns?
- What concise realized sequence should become the next logical authority?

On accept or concern, it returns:

- one realized sequence in the shared sequence family;
- significant Writer additions only;
- resulting scene state and unresolved threads;
- optional concise concerns.

On reject, it returns:

- typed material conflict classes;
- exact short quotes or uniquely identifiable offending text;
- a concise explanation;
- whether a fresh Writer attempt is appropriate.

Harmless lamps, furniture, posture, cadence, gaze that does not imply Ted participation, ambient texture, and vague low-stakes color are normally omitted from the realized sequence. They require no semantic category unless they become causally used, durable, contradictory, or future-relied-on.

Python verifies that quoted text exists, identities are known, supplied Ted claims match exact source when cited, and the output schema is mechanically valid. Python does not reinterpret the prose or override the Validator's semantic judgment.

## 7. Reader and acceptance

The independent Reader evaluates the complete visible prose for realism, voice, pacing, completeness, repetition, premature closure, and reader-visible problems. It does not build story authority or rewrite prose.

Only after Validator and Reader acceptance does Python:

- allocate deterministic IDs and hashes;
- bind the realized sequence to the branch and turn;
- write events, memory, relationships, material state, and current-scene state that the realized sequence explicitly authorizes;
- atomically commit or roll back.

## 8. DeepSeek capability-restricted logic route

The consensual-adult capability fallback uses the same sequence-first contract.

### Boundary

When Codex reaches a recognized capability restriction, Codex—not Python—produces the last supported safe boundary sequence containing:

- current causal sequence and scene state;
- factual present characters;
- current responder/floor state;
- relevant motives, feelings, knowledge, and unresolved threads;
- adult identity, consent, capacity, and freedom-to-stop state already established by accepted authority;
- the exact resumption boundary.

Python freezes and routes that package mechanically. Python does not infer consent, activity, psychology, or scene logic.

### Restricted interval

```text
Codex safe boundary sequence
+ exact creator prompt
+ relevant character material
-> DeepSeek Planner
-> DeepSeek Writer
-> DeepSeek Safe-Continuity Formatter
```

The DeepSeek Planner creates only the restricted-interval intended sequence. The Writer writes the visible prose. The Formatter compares the DeepSeek plan and exact prose and returns a neutral, non-graphic realized sequence in the shared sequence family:

- ordered material milestones;
- significant additions;
- owner-specific feelings and knowledge changes;
- consent/capacity and freedom-to-stop status;
- relationship/material/location/condition changes or explicit unknowns;
- resulting state, unresolved threads, and whether the restricted interval is complete.

The Formatter does not partition every sentence or produce canonical role ledgers. It has up to three independent attempts against frozen Writer bytes.

Raw capability-restricted prose is not sent to Codex. Codex can validate only the safe realized sequence against the pre-boundary sequence and cannot certify raw-prose fidelity. Creator review remains required. Python commits only the accepted safe realized sequence after mechanical checks and creator approval.

The first capability restriction opens a sticky bounded DeepSeek interval. CERA does not repeatedly call Codex for the same refusal. When the accepted safe realized sequence marks the interval complete, the normal Codex Planner resumes from that sequence.

## 9. Migration rule

The exhaustive Validator, compact span ledgers, and their historical evidence remain readable and immutable but are not the target runtime.

No further category-by-category prompt patch is justified merely because a harmless phrase fails an old DTO. New work must implement and qualify this sequence-first route provider-free before resuming SillyTavern or the twenty-turn campaign.
