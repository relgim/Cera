# SillyTavern depth, voice-context, and regeneration correction result

**Date:** 2026-07-30  
**Authority:** D-146, D-150, D-157  
**Scope:** local ordinary-route typed context and client lifecycle correction

## Outcome

Creator manual testing exposed four shared defects rather than a token-limit
problem:

1. Long and Epic were advisory prose, so a one-beat Reasoner decision remained
   compliant.
2. CERA correctly ignored SillyTavern system text, but did not independently
   seed the authoritative V1.2 story-start scenario on an empty branch.
3. Selected speech-system evidence reached the Composer context assembler, but
   exact evidence sections were double-encoded as a JSON string inside the
   DeepSeek DTO.
4. SillyTavern cleared the captured regeneration key during an intervening
   `CHAT_CHANGED` event, causing a correct idempotency conflict at CERA.

D-157 corrects the full contracts and lifecycle. It does not increase output
token ceilings or add a character-specific exception.

## Typed depth contract

`SceneDepthMode` now travels through `RawTurnEnvelope` v2,
`DevelopmentTurnSpec` v2, and `SceneReasonerRequest` v2. The Reasoner packet
contains one `scene_development_contract`:

- Off requests the smallest complete consequential exchange.
- Auto adaptively classifies atomic, developed ordinary, or domino/multi-scene
  scope.
- Long affirmatively requires the full supported current-scene causal chain.
- Epic affirmatively requires maximum supported NPC-controlled progression
  before the first genuinely necessary protected-user choice.

Every mode forbids numeric beat quotas, padding, repeated causality, invented
events, and protected-user invention. A short source cue cannot silently
downgrade Long or Epic.

## Authoritative initial scene

`HanezawaHumanTestWorld` resolves exactly one record tagged
`14_2_scenario_projection`. On an empty generation-zero branch, Python requires
that exact record's `source_text` in the seed dossier. It establishes the
September 2 arrival, Hanezawa house, expected resident, Sakura's initial floor,
and ringing doorbell without trusting the presentation shell.

The accompanying hard boundary treats named private starting states as
owner-bound. A selected character cannot acquire another character's private
state merely because the omniscient scenario projection contains both.

## Selected character realization context

The existing `ComposerContextAssembler` still selects only actual participants
and requires exactly one speech-system record per selected NPC. Inactive
characters remain absent. The DeepSeek DTO now decodes `sections_json` into a
real JSON object instead of embedding an escaped JSON document.

Composer prompt v18 explicitly teaches that a `character_voice` block contains
creator-authored speech rules and may contain illustrative lines. DeepSeek must
apply the named character's diction, syntax, rhythm, reasoning style, emotional
leakage, and relevant variation, while never copying an example or treating it
as historical dialogue. Identity, relationship, current-state, expression, and
continuity blocks constrain psychology and facts.

The provider-free live-shaped test proves that Sakura's internal packet
contains her precise/formal/direct baseline, her timing example, and the rule
that her speech remains administrative and reasoned.

## Regeneration lifecycle

The installed `Seraphina-Development-Memory` extension now retains a generated
regeneration key only while request routing is in flight. An intervening
`CHAT_CHANGED` cannot erase it; request-settings assembly consumes and clears
it. Normal later turns therefore cannot inherit a stale regeneration identity.

CERA independently removes the candidate head being replaced from the sibling
regeneration's seed continuity and labels that head non-authoritative for the
new candidate. Append topology, immutable sibling publication, idempotency,
and branch validation remain unchanged.

## Active versions

- Reasoner adapter `cera.codex_scene_reasoner.v13`;
- Reasoner packet `cera.codex_scene_reasoner_packet.v8`;
- Reasoner prompt `cera.codex_scene_reasoner_prompt.v13`;
- Composer adapter `cera.deepseek_scene_composer.v19`;
- Composer packet `cera.deepseek_scene_composer_packet.v12`;
- Composer prompt `cera.deepseek_scene_composer_prompt.v18`;
- Composer DTO `cera.deepseek_composition_draft.v6`;
- SillyTavern response-profile family `cera-sillytavern-*-v4`.

## Verification

- focused Reasoner, Composer, SillyTavern, and live-shaped pipeline tests:
  57/57;
- complete repository suite: 449/449 in 210.025 seconds;
- Python compilation: clean;
- installed extension `node --check`: clean.

No live provider call or story write was used for this provider-free
verification. The existing manual story database was not reset or overwritten.
The correction adds no retry, fallback, validator weakening, Adult ON/EX live
publication, production binding, promotion, external handler, or deployment.

## Advisory review

ChatGPT Pro review is requested as a prompt and architecture advisory. Codex
will independently assess the response; technical ownership remains unchanged.
