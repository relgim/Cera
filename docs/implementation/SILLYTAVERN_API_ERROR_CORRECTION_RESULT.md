# CERA SillyTavern API-error correction result

**Date:** 2026-07-29  
**Authority:** D-146 and D-150  
**Result:** corrected and verified through the actual SillyTavern UI

## Reported failure

The creator's first manual message, `Hello, hanezawa residence?`, was saved by
SillyTavern but did not receive an assistant message. The persistent
non-production world remained at generation 0, so the failed attempt did not
become accepted story state.

## Shared root causes and corrections

Two compatibility gaps were exposed.

1. CERA session controls depended on hidden prompt markers. SillyTavern now
   sends typed `cera_session_id`, `cera_scene_depth`, and optional
   `cera_regeneration_key` request fields. The CERA adapter treats typed
   metadata as primary, retains legacy markers only as a compatibility path,
   and rejects conflicts between the two representations.
2. DeepSeek could label an exact protected-user source segment with the wrong
   realization kind even when the source obligation allowed exactly one kind.
   Python now derives that unambiguous bookkeeping field before authoritative
   validation. Ambiguous or unauthorized cases remain provider-authored and
   fail closed; protected-user ownership and exact-source validation were not
   weakened.

The Composer adapter version advanced from
`cera.deepseek_scene_composer.v12` to `cera.deepseek_scene_composer.v13`.

## Verification

- A fresh disposable direct probe completed in one attempt with one
  Sol-medium Reasoner call, one DeepSeek V4 Pro Composer call, and one
  independent Sol-medium verifier call.
- The disposable probe committed generation 1 and accepted artifact
  `artifact:7affc04c-d715-58cd-a64c-c38c98347225`; its evidence is preserved
  under `evaluation/evidence/sillytavern_api_error_fix_2026-07-29_v1/`.
- The original saved message was then replayed once through the real
  SillyTavern interface. Sakura's response appeared normally, and CERA
  atomically committed generation 1 with accepted artifact
  `artifact:afaa036d-40d1-585b-8365-bffcf7cfc3a0`.
- The persistent human-test database reports one visible artifact,
  `integrity_check=ok`, and zero foreign-key findings.
- JavaScript syntax checks, focused adapter/Composer tests, and the complete
  provider-free suite passed. The terminal full-suite result is 442/442 in
  314.575 seconds.

The unrelated SillyTavern startup warnings for unavailable ComfyUI and Ollama
endpoints do not involve the CERA request path.

## Current boundary

The ordinary local manual route is working. This correction does not activate
live Adult ON/EX publication, production binding, route promotion, deployment,
or an external handler. No retry or fallback was added.
