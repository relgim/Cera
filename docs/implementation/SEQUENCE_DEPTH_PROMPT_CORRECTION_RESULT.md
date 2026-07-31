# Adaptive sequence-depth and Composer DTO v6 result

**Date:** 2026-07-29  
**Authority:** D-146, D-150, D-156  
**Scope:** local ordinary-route prompt and provider-contract correction

## Outcome

CERA now treats response depth as adaptive causal scope rather than a minimum
beat quota. Runtime Codex first distinguishes an atomic exchange, a developed
ordinary interaction, and a supported domino or multi-scene progression. It
owns every meaningful NPC-controlled causal beat through the earliest natural
user handoff. DeepSeek realizes those beats as complete prose without being
asked to invent connective logic.

The active versions are:

- Reasoner adapter `cera.codex_scene_reasoner.v12`;
- Reasoner prompt `cera.codex_scene_reasoner_prompt.v12`;
- Composer adapter `cera.deepseek_scene_composer.v18`;
- Composer packet `cera.deepseek_scene_composer_packet.v11`;
- Composer prompt `cera.deepseek_scene_composer_prompt.v17`;
- Composer DTO `cera.deepseek_composition_draft.v6`;
- SillyTavern response-profile family `cera-sillytavern-*-v3`.

No typed minimum-beat validator was added. A one-beat atomic reply remains
valid, while a consequential cue may carry four or more distinct current
beats or supported mini-scenes. Questions or motions serving the same tactic
without an intervening state change belong to one beat.

## Shared contract correction

The first substantive live probe exposed
`REALIZATION_AUTHORITY_OWNER_MISMATCH` and
`BEAT_AUTHORITY_RELATION_MISMATCH`. DeepSeek had been required to repeat the
character owner of each beat even though Python already owns that relationship.

DTO v6 removes `owner_id` from provider-authored realization references.
DeepSeek supplies a story function, segment key, and one advertised beat or
source authority ID. Python derives the character owner from the validated
authority map before running the unchanged domain obligations. Unknown or
missing authorities, missing beats or participants, invalid source/adult
coverage, and semantic realization failures still fail closed.

An exact duplicate of the same kind, segment, and authority is now removed by
Python before domain validation. It is redundant provider bookkeeping, not a
story claim. References that differ in kind, segment, or authority remain
distinct and validated.

The Composer prompt also now:

- realizes closely coupled beats in natural passages rather than one paragraph
  per planning function;
- forbids invented biography, historical frequency, household roles,
  institutional labels, and established routines without supplied evidence;
- treats ordinary protected-user setup as causal context rather than a prose
  checklist when creator-event coverage is false;
- begins from the selected NPCs' current reaction and forbids extending the
  protected user with a new movement, message, reply, expression, intention,
  or off-page activity.

## Live evidence

All calls were one-shot by stage, with no retry or fallback.

| Probe | Terminal result | Evidence |
|---|---|---|
| Atomic telephone greeting | Accepted | `artifact:0a2a0a60-8f9d-5be9-b2fb-65050b2037f2`; one Sakura beat; 274 characters |
| First substantive gift sequence | Rejected at Composer obligations | `request:f9d413c1-b31c-565e-8076-d1efbd7bf780`; owner/beat mismatch; zero story writes |
| DTO v6 substantive sequence | Rejected at typed decoding | `request:7122276d-0de1-50c8-95f2-fff3650befb0`; exact duplicate realization bookkeeping; zero story writes |
| Normalized DTO v6 sequence | Rejected by independent verifier | `request:c5d4829e-fe2f-55b5-98a9-62d5a947e0ae`; unsupplied protected-user realization; zero story writes |
| Final substantive gift sequence | Accepted | `artifact:963e1ec1-1af7-5803-ae3b-a4fc2deced6c`; Mia and Sakura; four realized beats; 2,540 characters |

The accepted substantive scene forms a visible causal chain: Mia discovers and
opens her gift, calls Sakura into the moment, states her own response, Sakura
inspects and answers on her terms, and Mia independently acts on her decision.
It stops without adding a new Ted action. The atomic greeting remains concise,
so the correction increases supported development rather than padding every
reply.

SQLite reports `integrity_check=ok` and zero foreign-key findings. The local
human-test database contains ten isolated branches, eight accepted artifacts,
and three immutable failure-evidence bundles. Existing creator chat branches
were not reset or overwritten.

## Verification

- focused prompt/DTO/integration tests: 77/77 before exact-duplicate handling;
- focused normalization/integration tests: 48/48;
- focused final prompt tests: 70/70;
- complete final repository suite: 446/446 in 253.825 seconds.

These results qualify the correction for continued local ordinary-route human
testing only. They do not activate Adult ON/EX publication, production
binding, promotion, deployment, retry, fallback, or an external handler.
