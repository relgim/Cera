# CERA external review handoff — sequence-first runtime

**Review target:** the current CERA feature/review branch published from `D:\CP25\source`  
**Creator:** Ted  
**Review purpose:** independent architecture review before further live-provider qualification

## Product goal

CERA is a roleplay runtime that separates reasoning, prose generation, semantic review, quality review, and durable state. The intended normal route is:

```text
User source
-> deterministic Python mechanical custody
-> persistent Codex Scene Planner
-> stateless DeepSeek Writer
-> independent Codex Validator
-> independent Reader
-> deterministic Python atomic commit
```

The system must preserve character realism, accepted continuity, private knowledge, branch separation, and Ted's control over his own unsupplied dialogue, actions, thoughts, emotions, consent, and decisions.

## Why the architecture is being revised

Earlier Vera/Sera/CERA iterations repeatedly assigned semantic judgment to Python or asked probabilistic models to reproduce large exact metadata contracts. The resulting failure pattern was:

```text
Python or a model makes one semantic classification
-> another component treats it as exact authority
-> harmless prose or a different valid representation fails a DTO/schema rule
-> prompts and schemas grow
-> latency and false-positive rates grow
```

A recent accepted two-turn route took roughly six minutes per user turn. The exhaustive Validator received about 21,000 input tokens and produced 9,000–11,000 output tokens to classify an 849–1,700 character story. It partitioned nearly every character of prose into exact spans and repeated ownership data across multiple structures. This generated many contract failures without improving the core product enough.

Python-selected `active_cast_ids` and mandatory full character-record retrieval also risk magnifying subtle background characters. For example, a character who is merely present can become disproportionately active if Python labels them an active responder before the Planner reasons about the scene.

## Controlling direction: sequence-first runtime

The active design authority is `docs/authority/CERA_SEQUENCE_FIRST_RUNTIME_V1.md`.

### Python

Python owns only mechanical and transactional truth:

- exact user bytes and typed source units;
- branch, turn, candidate, record, and provider identities;
- hashes, revisions, visibility, storage, call accounting, and atomic commit/rollback;
- the factual set of characters explicitly established as present or eligible.

Python must not decide:

- narrative salience;
- who should respond;
- who should remain backgrounded;
- psychology or causal logic;
- whether a prose addition is important;
- semantic ownership or meaning.

### Codex Planner

The persistent Planner receives a compact accepted realized sequence, current resulting state, user message, present/eligible characters, and only relevant changed evidence. It decides:

- which NPCs act, speak, react, or remain inactive;
- causal and psychological sequence;
- relevant private-state direction;
- stopping point before Ted's next unsupplied choice.

Routine continuation should not automatically trigger full reads of every present character record.

### DeepSeek Writer

DeepSeek receives a compact plan and relevant voice/context material and writes prose. It does not return ownership ledgers, hashes, offsets, memory records, active-cast authority, or acceptance decisions. DeepSeek variability is accommodated through at most three fresh attempts against one frozen package, with no output merging.

### Codex Validator

The Validator reads the Planner's intended sequence, DeepSeek's exact prose, the prior accepted state, present/eligible character facts, and exact current user source. Its normal job is:

1. determine whether the intended causal sequence is materially realized;
2. identify hard conflicts or materially important additions;
3. return one concise realized sequence and resulting state.

It should not provide a gap-free semantic partition of harmless prose. Minor atmosphere, posture, gestures, incidental furnishings, and harmless wording remain visible but are omitted from durable authority unless they become causally or durably important.

### Reader and commit

The Reader judges whole-response quality. Python validates the returned structure mechanically and atomically commits only the accepted realized sequence and durable changes.

## Current status

- Small live Stage 4 qualification: passed.
- Two-turn `Continue the scene` qualification: passed.
- Isolated SillyTavern and the mandatory twenty-turn campaign: not yet completed.
- The former compact/exhaustive Validator patch cycle is superseded before further provider calls.
- The current task is provider-free sequence-first migration, fake-pipeline proof, one complete suite, then a bounded live canary.
- No production route, installed SillyTavern, deployment, merge, or production database mutation is authorized.

Historical evidence and prior contracts remain useful for regression cases but should not constrain the new runtime into preserving unnecessary wire complexity.

## Deferred consensual-adult capability route

The future capability-restricted route is documented in `docs/authority/CERA_CONSENSUAL_ADULT_CAPABILITY_FALLBACK_V1.md`.

When a consent-valid adult request reaches a Codex capability boundary:

```text
Codex safe boundary sequence
-> DeepSeek Planner
-> DeepSeek Writer
-> DeepSeek Safe-Continuity Formatter
-> Codex reviews only the safe retained sequence
-> Reader/creator review
-> Python mechanical commit of accepted safe fields
```

Raw restricted prose does not become automatic memory authority and is not sent to Codex. The Formatter should retain only neutral sequence, owner-specific state, knowledge/material changes, consent/capacity status, ending state, and unresolved threads. Creator acceptance remains necessary because Codex cannot certify raw prose it did not inspect.

## Requested Claude review

Please inspect the code rather than accepting this handoff as proof. Focus on the active sequence-first migration and identify where the current implementation still contradicts it.

Prioritize these files and their call sites:

```text
docs/authority/CERA_SEQUENCE_FIRST_RUNTIME_V1.md
docs/authority/CERA_RUNTIME_MODEL_V3.md
docs/authority/CERA_CONSENSUAL_ADULT_CAPABILITY_FALLBACK_V1.md
src/cera/continuous/runtime.py
src/cera/continuous/prompting.py
src/cera/continuous/provider.py
src/cera/continuous/evidence.py
src/cera/continuous/contracts.py
src/cera/continuous/packets.py
src/cera/sillytavern/campaign.py
```

Please return:

1. **Architecture blockers:** places where Python still decides responder salience, psychology, semantic meaning, or prose importance.
2. **Redundant model-visible custody:** hashes, receipts, manifests, and repeated evidence metadata that Python can retain privately.
3. **Validator simplification:** the smallest accepted/rejected sequence contracts that still protect Ted, cast/knowledge boundaries, required beats, continuity, and stopping point.
4. **Planner continuation simplification:** how to preserve persistent context without full character rereads or Python-selected active responders.
5. **Database boundary:** how Python can mechanically commit Codex's accepted realized sequence without becoming the semantic judge.
6. **DeepSeek route review:** whether both the normal Writer route and the deferred adult capability route keep DeepSeek's responsibilities bounded and tolerate irregular prose.
7. **Migration plan:** minimal reversible steps, compatibility strategy for historical evidence, focused tests, and the first bounded live canary.
8. **Risks or disagreements:** call out bad assumptions directly rather than preserving them for compatibility.

Classify findings as current blocker, important before SillyTavern, production hardening, or deferred. Do not recommend adding another phrase-specific Writer/Validator prompt rule unless it resolves a demonstrated general contract contradiction.
