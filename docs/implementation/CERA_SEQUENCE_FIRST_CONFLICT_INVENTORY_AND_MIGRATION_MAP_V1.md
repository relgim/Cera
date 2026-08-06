# CERA Sequence-First Conflict Inventory and Migration Map V1

Status: provider-free migration authority record

Authority:

- `docs/authority/CERA_SEQUENCE_FIRST_RUNTIME_V1.md`
- `docs/authority/CERA_SEQUENCE_FIRST_IMPLEMENTATION_REFINEMENT_V1.md`
- Queue 0060 provider-free correction and review-publication authority

Inspected source freeze:

- Git commit: `be81cbb454e1edd219fd03cf3c0924bc716a85c2`
- Git tree: `dfdbfe1420c1097e995034fb4672b39b5701890a`
- Review branch: `review/cera-sequence-first-runtime-v1-20260805`

This document is an inventory and migration contract. It does not activate a
runtime route, dispatch a provider, mutate a story database, or reinterpret
historical evidence.

## Controlling separation

CERA must keep three concepts distinct:

1. **Factual presence** is authoritative input from accepted scene state.
2. **Narrative participation and salience** are Planner decisions.
3. **Mechanical custody and persistence** are Python responsibilities.

Python may validate identities, set relationships, hashes, branch scope, and
transaction structure. It must not decide who ought to speak, infer psychology,
classify prose meaning, or create semantic truth from prose.

## Conflict inventory

| Current surface | Material conflict | Sequence-first disposition |
| --- | --- | --- |
| `LeanContinuationAuthorityV1.active_cast_ids` and `build_accepted_lean_continuation_authority` | Python derives active cast by unioning prior involved identities. Prior involvement is neither current factual presence nor a decision that the character should respond now. | Remove this field from the new route. Supply only accepted factual presence. Planner chooses salience by assigning owners to intended items. Python derives responder and background observability from those owner choices; neither set is model-authored. |
| `ContinuousPlannerTurnPacketV1` custody, receipt, hash, adapter, and binding payload | Provider-visible mechanical baggage consumes context and encourages the model to reason about transport custody instead of the scene. | Keep the full receipt envelope private to Python. Expose only opaque stable evidence keys and semantically useful source content required for the current decision. Verify all private bindings before dispatch and all returned references after response. |
| `CharacterRoleLedgerV1` plus `RichSequenceBeatV1` | Seven mutually exclusive role arrays force ordinary compound behavior into artificial micro-beats and duplicate semantic ownership across later Validator and finalization records. | Replace with one compact shared item family. An item has one primary owner when needed, a small kind, concise intended/realized meaning, causal parent, exact semantic references, and only material changes. There are no affected, observing, addressed, or referenced mini-ledgers. |
| Planner `RichPlannerSequenceV1`, Writer brief, Validator `FinalSequenceV1`, final fields, event candidate, and world edits | The same scene meaning is restated in multiple DTOs and can disagree between planning, validation, event extraction, and persistence. | Use one conceptual `SequenceV1` family. Planner creates an intended sequence; Validator either rejects with a material conflict or returns the accepted realized sequence. Python mechanically allocates event and storage identities from that realized sequence without inventing or re-summarizing meaning. |
| Validator realization segments, protected adjudications, field scopes, diagnostic partitions, exact offsets, and gap-free coverage | The Validator is acting as a full prose parser and bookkeeping generator. This is slow, brittle, and causes harmless prose to fail structural contracts. | Replace with binary accept/reject. Accepted output returns one realized sequence plus orthogonal review flags. Rejected output returns one closed material conflict plus the shortest exact quote or omitted Planner item. No exhaustive offsets, span partition, harmless-prose enumeration, field scopes, duplicate coverage, or duplicate event record. |
| `ValidatorFinalizationPackageV1`-coupled world promotion | Atomicity is strong, but persistence is coupled to the redundant finalization graph and encourages Python to treat generated bookkeeping as semantic authority. | Preserve atomic branch promotion. Add a sequence-first commit projection whose semantic input is only the validated realized sequence plus Python-owned allocation and receipt data. Derived indexes and summaries remain rebuildable views. |
| `select_scoped_candidate_cast_shadow` lexical/accepted-state selection | Lexical mention and previous activity can silently become cast authority. This collapses candidates, presence, and salience. | Do not use it on the sequence-first route. Python may assemble eligible evidence candidates, but accepted scene state supplies factual presence and Planner owns responder selection. |
| Automatic complete character/card context and repeated accepted-history envelopes | Context grows even when no character revision or indirect-memory need exists. Repetition increases latency and can reinforce stale interpretations. | Send current public scene state, prior realized sequence, changed character deltas, and relevant unresolved threads. Full character/card reads occur only on initialization, first present participation, changed revision, explicit evidence need, or bounded Planner lookup. |
| Provider conversation state as practical continuation context | A fast retained thread can accumulate malformed schemas or stale provisional decisions. | Provider state is a cache only. Every turn is reconstructible from Python packets and accepted artifacts. The accepted realized sequence and branch head, not provider history, are restart authority. |
| Reader checkpoint | Reader can drift into a second authority validator or style optimizer. | Retain a small whole-response quality gate. It judges only severe reader-facing failure after semantic acceptance and cannot change sequence truth. |
| Consensual-adult and protected capability routing | A separate content route can create a second incompatible memory/event model. | When authorized and implemented, it must return the same safe realized-sequence contract. Restricted provider detail may remain unavailable to Codex, but accepted non-graphic consequences and state changes use the common sequence and transaction path. This migration does not activate that deferred route. |

## Semantic ownership map

| Responsibility | Owner | Output authority |
| --- | --- | --- |
| Source bytes, ingress identity, factual scene presence, branch head, accepted-artifact loading | Python | Authoritative mechanical and accepted-state input |
| Evidence search terms and bounded follow-up retrieval | Planner through bounded tools | Advisory until exact evidence is fetched and cited |
| Responders, conversational floor, psychology, causal sequence, scene stopping point | Planner | Intended semantic plan; provisional until realization is validated |
| Visible prose | DeepSeek Writer | Creative candidate only |
| Intended-versus-realized semantic comparison, material conflict detection, realized sequence | Codex Validator | Authoritative semantic acceptance decision for the candidate |
| Whole-response minimum quality | Codex Reader | Acceptance gate only; no semantic rewrite |
| IDs, hashes, provider accounting, event allocation, atomic commit, rollback, fork, restart recovery | Python | Authoritative mechanical custody |
| Durable canon | Creator-accepted branch transaction | Authoritative only after acceptance and atomic commit |

## Additive target contracts

The new route must be additive and versioned. Existing V3/V6 contracts and
evidence remain readable and unchanged.

### `SequenceFirstTurnSemanticInputV1`

Model-visible fields are limited to:

- exact current user source and protected-user claim keys;
- exact factual `present_character_ids`;
- current public scene state;
- prior accepted realized sequence, when one exists;
- changed character/card deltas relevant to present participants;
- retrieved authoritative evidence records with opaque evidence keys;
- relevant unresolved threads and hard boundaries;
- approved opaque durable-target keys.

World, branch, scene, turn, request, candidate, parent, hashes, filesystem
locators, database locators, provider receipts, adapter identities, prompt
hashes, transaction paths, and private transport custody stay in
`SequenceCustodyEnvelopeV1`. Python attaches that envelope only after semantic
decode and closed-reference validation.

### `SequenceDraftV1`

One shared conceptual family represents either `intended` or `realized`
semantics. It contains:

- ordered compact items;
- resulting public state;
- material, knowledge, relationship, and presence changes;
- unresolved threads;
- stopping boundary.

Each item contains:

- a local item key;
- a closed item kind;
- one primary owner or speaker when the beat asserts character-controlled
  behavior or private state;
- concise semantic meaning;
- an optional causal parent;
- exact evidence keys used for hard decisions;
- exact protected-user claim keys when supplied protected content is referenced;
- approved opaque target keys for durable changes;
- exact Planner item references on realized items only;
- only changes that matter beyond decorative prose.

Responder IDs are derived from intended-item NPC owners. Backgrounded NPCs are
derived from accepted presence minus those responders. Coverage is derived as
the union of realized-item Planner references. None is a provider-authored
field.

Python validates structure, identity membership, causal-key integrity, evidence
key existence, protected-claim exactness, and visibility/knowledge ownership. It
does not judge whether the beat is psychologically apt or narratively salient.

### Writer and validation contracts

`SequenceFirstWriterBriefV1` contains the intended sequence, relevant voice
cues, a concise continuity anchor, prose boundaries, and the stopping point. It
contains no event IDs, persistence proposal, or Validator bookkeeping.

`ValidatorDecisionV1` has exactly two outcomes:

- `accept`: one realized sequence, optional orthogonal review flags, and no material conflict;
- `reject`: no realized sequence, one closed material conflict class, and the
  shortest exact offending quote or an omitted-beat key.

`ReaderVerdictV1` accepts, rejects, or is inconclusive based only
on severe reader-facing quality. It does not emit a replacement sequence.

## Persistence projection

After Validator and Reader acceptance, Python performs a deterministic
projection:

```text
accepted Writer prose
+ validated realized SequenceDraftV1
+ Python-private custody envelope
+ creator acceptance decision
-> immutable branch artifact
-> mechanically allocated event/state rows
-> atomic branch-head promotion
-> rebuildable summaries and indexes
```

The projection may copy exact semantic fields from the realized sequence. It
must not infer a new event, rewrite a character motive, summarize unvalidated
prose, or promote decorative Writer detail.

## Migration order

1. Freeze this conflict inventory and semantic ownership map.
2. Add the sequence-first contracts, concise prompts, provider-neutral ports,
   fake providers, and a sequence-first projection onto the existing atomic
   store behind an unpromoted route.
3. Prove the provider-free boundary matrix, including ordinary continuation,
   indirect memory, multi-character presence versus participation, protected
   user autonomy, regeneration, fork isolation, restart reconstruction,
   Writer rejection, Reader rejection, provider failure, and atomic rollback.
4. Add the real Python packet projection and real atomic store adapter while
   preserving the old route and historical readers.
5. Run one complete repository suite at the frozen source checkpoint.
6. Only after the suite passes, use the separately bounded live canary authority.
7. On canary success, run two isolated SillyTavern qualifications and the
   mandatory disposable twenty-turn campaign.

No stage may silently promote the route, merge history, mutate production,
invent a fallback, or use provider conversation state as durable memory.

## Provider-free acceptance gates for the compact fake pipeline

The first implementation checkpoint must prove:

1. exact factual presence does not force every present character to respond;
2. Planner selection cannot add an absent character without explicit remote
   participation authority;
3. private-state beats have one matching knowledge owner;
4. protected-user assertions require exact current source claims;
5. the Writer receives no persistence authority;
6. accepted validation returns one realized sequence without span partitions;
7. rejection identifies only material conflict evidence;
8. Reader rejection cannot rewrite or commit;
9. rejected Writer attempts never enter ancestry or memory;
10. at most three independent Writer attempts are permitted only for
    Writer-attributable Validator or Reader rejection;
11. transport, Python, authority, branch, and accounting failures never trigger
    another Writer call;
12. regeneration creates an immutable sibling candidate;
13. restart reconstruction requires only accepted artifacts and Python custody;
14. branch forks cannot retrieve sibling-private evidence;
15. atomic commit failure leaves branch head and durable state unchanged.
