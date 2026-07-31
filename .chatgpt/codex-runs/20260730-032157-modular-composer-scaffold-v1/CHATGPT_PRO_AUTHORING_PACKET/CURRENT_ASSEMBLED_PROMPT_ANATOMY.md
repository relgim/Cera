# Current assembled Composer prompt anatomy

This describes the active route at packet freeze time. The modular compiler is
not connected to it.

## Active identity

- Composer adapter: `cera.deepseek_scene_composer.v19`
- Composer prompt: `cera.deepseek_scene_composer_prompt.v18`
- Composer packet: `cera.deepseek_scene_composer_packet.v12`
- Implementation model default: `deepseek-v4-flash`
- Active static system bytes: `10317`
- Active static system words: `1413`
- Active static system SHA-256: `01e2c7d2ae1021e0a1001f2eaa619960ae057a7db2b2e44c4c01912b2e388681`

## Current message assembly

1. `build_deepseek_composer_messages()` emits one large static system message.
2. It emits one user message containing canonical JSON from
   `build_deepseek_composer_packet()`.
3. The JSON packet contains `schema_version`, `prompt_version`,
   `composition_dto`, `authority_policy`, `segment_policy`,
   `output_obligations`, and `output_schema`.
4. `composition_dto` contains identity, exact source, binding decision/current
   beats, scene scope, response profile, Python-owned Depth contract,
   creator-event coverage, hard boundaries, optional Adult specificity, and
   selected realization context.
5. Character voice/expression and Adult craft are already selected before
   DeepSeek. DeepSeek does not retrieve files or choose examples.

## Mixed concerns in the current static system message

The active system text currently combines stable authority, protected-user
rules, biography/history restrictions, Depth realization, character voice,
Adult specificity, future-segment handling, JSON segment semantics, coverage
obligations, and output bookkeeping. The new seam separates these concerns but
does not replace this active text.

## Confirmed manifest correction

`config/cera/prompts/MANIFEST.json` previously bound the character-expression
source to Composer prompt v6 although active prompt v18 embeds the source hash.
The frozen source copy contains the corrected v18 binding. The source artifact
and active prompt text were not modified.
