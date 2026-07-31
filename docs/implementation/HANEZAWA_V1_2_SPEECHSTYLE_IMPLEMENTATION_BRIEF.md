# CERA — Hanezawa Genesis V1.2 Speech-Style Implementation Brief

**Role:** Advisory creator-intent and prompt-architecture handoff for Codex technical review  
**Repository:** `D:\AIChatBot\Cera`  
**Creator-approved source:** `genesis/cera_authority/HANEZAWA_CORE_GENESIS_V1_2.md`  
**Proposed prompt module:** `config/cera/prompts/character_specific_speech_realization_v1_2.txt`  
**Status:** Handoff only. This file does not authorize provider calls, production binding, SillyTavern activation, deployment, push, or automatic promotion.

## 1. Creator goal

The creator considers the V1.2 refusal, dignity, family-defense, trust-fracture, and identity-preserving examples the target speech-quality standard.

The desired result is not merely that seven women have different catchphrases. Their dialogue should differ because each woman:

- identifies a different moral injury;
- protects a different part of herself or the family;
- uses different evidence and comparisons;
- has different sentence rhythm, formality, directness, and emotional strategy;
- responds differently to surprise, anger, disappointment, partial trust loss, and complete trust destruction;
- remains recognizably herself during romance, possessiveness, yandere development, or extreme M development.

The examples in Genesis are illustrative authoring evidence. They are not exact expected prose, phrase templates, or future events.

## 2. Preferred implementation architecture

The most efficient and generalizable implementation is:

```text
Authoritative Genesis records
→ Character Director selects meaning, speaker, target, boundary, trust effect, and intensity
→ Python validates decision and ownership
→ compact active-speaker speech packet
→ DeepSeek Composer/Writer realizes fresh prose using the generic speech contract
→ existing Python participant, protected-user, semantic, and integration validation
```

Do **not** inject the complete V1.2 Genesis or every dialogue example into every Writer request.

Do **not** implement named phrase branches such as:

```text
if Sakura and objectification -> output legal metaphor
if Tomi and refusal -> output race metaphor
```

Instead, compile reusable character-owned speech guidance and project only the active speakers' relevant fields.

## 3. Recommended compiled speech fields

Codex should map the approved source into the repository's existing authority and schema design. The exact schema is Codex-owned, but the runtime projection should preserve the equivalent of:

```text
speaker_id
baseline_voice
sentence_rhythm
vocabulary_and_formality
emotional_strategy
moral_frame
response_mode
selected_intent
exact_boundary_or_violation
target_of_protection
relationship_context
current_trust_state
emotional_intensity
metaphor_domains
identity_invariants
latent_capacity_relevance
forbidden_drift
style_example_ids
```

`style_example_ids` should resolve to zero, one, or at most two relevant examples. Examples are not authoritative output strings. They demonstrate construction and reasoning only.

## 4. Director versus Writer ownership

### Character Director owns

- who responds;
- why this speaker owns the response;
- the exact semantic answer or boundary;
- whether the response protects self, Mother, or a sister;
- whether the moral problem is objectification, entitlement, coercive pressure, betrayal, or another issue;
- response intensity;
- current trust-fracture state;
- whether a metaphor or comparison would serve the selected tactic;
- whether latent-M identity preservation is relevant without weakening refusal.

### DeepSeek Writer/Composer owns

- fresh natural wording;
- sentence rhythm;
- dialogue and scene-local interiority;
- character-specific comparison or metaphor;
- subtext and emotional texture;
- visible action consistent with the selected meaning and scene facts.

### Python retains

- participant eligibility;
- knowledge and privacy;
- protected Ted authority;
- refusal/consent semantics;
- typed decoding;
- anchors and event ownership;
- durable truth and state integration;
- branch publication.

## 5. Why the generic Writer prompt is the correct seam

The creator wants this quality across many situations, not only objectification scenes. The stable rule belongs in the generic Composer/Writer realization contract, while character-specific content belongs in compact per-turn packets.

This avoids:

- seven named hardcoded prompt branches;
- copying examples verbatim;
- inflating every request with the full Genesis;
- making Character Director write finished prose;
- relying on Python to generate creative metaphors;
- allowing Writer style to change selected meaning.

The proposed module at:

```text
genesis source: genesis/cera_authority/HANEZAWA_CORE_GENESIS_V1_2.md
prompt source: config/cera/prompts/character_specific_speech_realization_v1_2.txt
```

may be adapted to the actual prompt-source ownership and manifest conventions after Codex inspects the repository.

## 6. Required semantic invariants

Implementation must preserve:

1. Refusal remains refusal.
2. Attraction is not automatically objectification.
3. Objectification means reduction, entitlement, or disregard of personhood/boundary—not merely sexual vocabulary.
4. Bodily response, fantasy, masturbation, love, prior intimacy, yandere potential, and latent M capacity never establish consent.
5. Consensual role framing does not become durable belief that the woman is inherently inferior or only a body.
6. Partial trust fracture is distinguishable from complete trust destruction.
7. Complete trust destruction may be irreversible.
8. Family bonds remain nonsexual.
9. Yuuni remains an adult without child framing.
10. Ted's private state and actions remain user-authored.
11. Illustrative examples are never copied as mandatory prose.
12. No future violation is scheduled merely because Genesis contains a hypothetical voice test.

## 7. Speech-quality acceptance standard

A successful response should let a reviewer identify the speaker from:

- syntax and sentence length;
- vocabulary and formality;
- metaphor domain;
- confrontation style;
- moral reasoning;
- how she protects family;
- embarrassment and anger pattern;
- treatment of trust and consequence.

The same selected moral meaning should not produce interchangeable dialogue.

Examples of intended differentiation:

- Hana uses care, home, doors, food, gardens, and family dignity.
- Sakura uses authority, evidence, procedure, judgment, contracts, and logical inconsistency.
- Mia uses service, tea, books, homes, chosen heroines, and the difference between care and consumption.
- Enne uses models, variables, access, permissions, systems, and evidence.
- Tomi uses races, teams, whistles, training, endurance, and fair play.
- Aoi uses presentation, leverage, circles, mirrors, positioning, and consequence.
- Yuuni uses stages, dolls, menus, songs, games, and blunt literal contradiction.

These domains are tendencies, not mandatory metaphor generators.

## 8. Required evaluation

Add or extend provider-free tests and offline evaluation for:

- all seven active speech profiles compile;
- all eleven response modes are representable;
- refusal remains refusal under latent-M and romantic context;
- partial and complete trust states project distinctly;
- wrong-owner private history does not enter the packet;
- only selected present speakers receive guidance;
- no Ted private psychology is injected;
- style examples are clearly labeled non-copyable;
- packet size remains bounded;
- every example ID resolves to creator-authorized source;
- no named phrase-specific runtime branch is introduced.

Add a blind human or model-assisted review set in which names and colors are removed. Evaluate:

1. speaker attribution;
2. semantic fidelity;
3. moral-position clarity;
4. metaphor naturalness;
5. non-copying;
6. voice distinction;
7. identity preservation under high intensity.

Do not weaken existing semantic validators to improve prose scores.

## 9. Ready-to-send Codex task

Copy the following instruction to Codex after the repository is clean and the V1.2 source is placed at the intended path:

```text
CERA HANEZAWA V1.2 SPEECH-STYLE INTEGRATION REVIEW AND IMPLEMENTATION

Work only in D:\AIChatBot\Cera. Preserve all existing work and inspect repository-local authority, schemas, prompt manifests, Director packets, DeepSeek Composer/Writer prompts, tests, and current Genesis compilation before editing.

Creator-approved source:
D:\AIChatBot\Cera\genesis\cera_authority\HANEZAWA_CORE_GENESIS_V1_2.md

Advisory prompt module:
D:\AIChatBot\Cera\config\cera\prompts\character_specific_speech_realization_v1_2.txt

Goal:
Integrate the V1.2 embodied-identity, refusal, dignity, family-defense, trust-fracture, and identity-preserving speech standard so DeepSeek produces fresh, unmistakably character-specific wording. Preserve the selected semantic decision; do not make DeepSeek choose participants, consent, trust promotion, durable truth, or Ted's private state.

Preferred architecture:
- compile creator-authorized speech/worldview fields into existing typed Genesis authority;
- let Character Director select response owner, moral meaning, boundary, target, intensity, and trust effect;
- project a compact active-speaker speech packet to DeepSeek;
- place the generic realization requirements in the Composer/Writer prompt seam;
- use at most zero to two relevant examples as explicitly non-copyable style evidence;
- keep all existing Python semantic, participant, privacy, consent, and protected-user validation authoritative.

Required behavior:
- refusal remains refusal regardless of attraction, arousal, love, past intimacy, fantasy, yandere potential, or latent M capacity;
- respectful attraction remains distinguishable from objectification;
- partial trust fracture and complete trust destruction render differently;
- speech uses character-natural reasoning, comparisons, and metaphor domains without forced catchphrases;
- examples are never copied verbatim or treated as historical dialogue;
- all seven voices remain recognizable under anger, jealousy, possessiveness, and extreme M development;
- no case-specific phrase branch, validator weakening, retry expansion, fallback expansion, provider call, story call, production binding, SillyTavern activation, deployment, push, or promotion is authorized by this task.

Add focused provider-free tests and a blind voice-evaluation artifact. Report exact changed files, prompt/packet version changes, source hashes, tests, packet-size effects, unresolved issues, and whether the implementation remains shadow/offline or affects an active route. Codex remains technical owner and may adapt field names or prompt placement when repository evidence supports a better implementation, but must preserve the creator goal and explain material deviations.
```

## 10. Suggested verification commands

Codex should use repository-discovered focused tests. At minimum, after implementation and without provider calls:

```powershell
Set-Location "D:\AIChatBot\Cera"
git status --short --branch
& ".\.venv\Scripts\python.exe" -m compileall -q src tests
& ".\.venv\Scripts\python.exe" -m pytest tests -q
git diff --check
git status --short --branch
```

If the repository provides bounded focused, smoke, or exact provider-free gates, run those instead of inventing new command names and report their actual totals.

## 11. Non-goals

This handoff does not request:

- live DeepSeek or Codex calls;
- story generation;
- exact phrase enforcement;
- new retries or fallbacks;
- weaker validation;
- a named-character decision branch;
- adult-route activation;
- migration of live user state;
- deployment or publication.
