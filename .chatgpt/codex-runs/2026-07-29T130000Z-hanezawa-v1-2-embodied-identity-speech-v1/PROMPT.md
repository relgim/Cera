# CODEX TASK — Compile Hanezawa Core Genesis V1.2 and project character-specific moral speech into CERA

Run ID: `2026-07-29T130000Z-hanezawa-v1-2-embodied-identity-speech-v1`

Repository: `D:\AIChatBot\Cera`

## Role and ownership

Ted has approved the Hanezawa V1.2 embodied-identity revision. ChatGPT Pro authored the creator-facing source and this implementation brief. Codex remains technical owner and must independently inspect the actual repository, select the smallest compatible implementation seam, preserve domain authority, and report any material deviation.

This is a **provider-free Genesis, retrieval, packet, prompt-source, and validation task**. It does not authorize live story calls, retries, fallback changes, provider-schema weakening, product activation, SillyTavern deployment, database mutation outside disposable test fixtures, commit, push, or promotion.

## Creator-authoritative inputs

Review and ingest:

```text
D:\AIChatBot\Cera\genesis\cera_authority\HANEZAWA_CORE_GENESIS_V1_2.md
D:\AIChatBot\Cera\genesis\cera_authority\HANEZAWA_VISUAL_CANON_ANIMA_V1.json
D:\AIChatBot\Cera\genesis\cera_authority\UserResponse.txt
```

`HANEZAWA_CORE_GENESIS_V1_1.md` remains immutable prior-version evidence. Do not overwrite or silently mutate it.

The V1.2 source now requires at minimum:

- 7 Hanezawa character modules;
- 42 directional family relationships;
- 7 Hanezawa-to-Ted starting models;
- 49 seven-deadly-sin lenses;
- 79 anchor events;
- 84 owner-specific Genesis memories;
- embodied-identity and sexual-ethics beliefs for all seven women;
- objectification and respectful-attraction distinctions;
- partial, severe, and complete trust-fracture behavior;
- character-specific metaphor/comparison domains;
- non-graphic refusal, dignity, retaliation, and family-defense speech standards;
- Hana's aging-related self-blame as an owner-private false causal belief;
- owner-safe daughter responses that do not leak affair knowledge;
- a DeepSeek realization standard that produces fresh character-specific dialogue rather than copied examples.

## First actions

Before editing:

1. Read `AGENTS.md`, current architecture authority, active CERA status, prompt/packet contracts, Genesis compiler/loader, retrieval visibility rules, Character Director packet creation, DeepSeek Composer/Writer prompt sources, current provider-profile manifest, and relevant tests.
2. Run and record:

```powershell
Set-Location "D:\AIChatBot\Cera"
git status --short --branch
git rev-parse HEAD
git branch --show-current
& ".\.venv\Scripts\python.exe" --version
& ".\.venv\Scripts\python.exe" -m pip --version
```

3. Do not overwrite unrelated dirty work. If the worktree contains overlapping edits from another active task, preserve them and stop with a precise blocker rather than resetting or stashing.
4. Verify the exact hashes of the V1.2 and visual source before compilation and record them in the result.

## Product goal

The creator considers this speech standard central to character quality. The final visible prose should reveal **which specific Hanezawa woman is speaking** through:

- sentence construction;
- emotional strategy;
- moral reasoning;
- what dignity or relationship she is defending;
- metaphor and comparison domain drawn from her lived identity;
- confrontation and retaliation style;
- trust consequence;
- current relationship and branch history.

The same refusal or objectification event must not produce seven interchangeable speeches with only the names changed.

Examples in Genesis are high-value **voice and reasoning evidence**, not executable dialogue, expected exact outputs, phrase templates, or future events. The runtime must generate fresh wording from the selected meaning and current scene.

## Architecture requirement

Preserve this ownership split:

```text
Creator-authoritative Genesis/data
→ owner-safe retrieval and current state
→ Character Director selects interpretation, intent, moral claim, intensity, tactic, and trust consequence
→ Python validates and compiles a concise expression brief
→ DeepSeek Writer/Composer realizes fresh dialogue and prose
→ Python validates/integrates accepted output
```

Do not move durable truth, participant eligibility, consent inference, trust promotion, or Ted's private state into DeepSeek.

### Preferred efficient seam

Use the smallest existing compatible seam after repository inspection. Prefer **not** to change the authoritative Python/domain response schemas merely to carry stylistic guidance.

The intended solution is usually:

1. Store the rich V1.2 beliefs, examples, trust logic, memories, and rhetorical signatures in Genesis/compiled data.
2. Let the Character Director select the current semantic meaning using existing typed decision fields wherever possible.
3. Have Python project a compact, consumer-specific expression block into the DeepSeek packet only when relevant.
4. Keep the full example library out of ordinary runtime context.
5. Let DeepSeek realize wording freely within the selected meaning and hard constraints.

If the current architecture already has generic fields for decision intent, tactic, subtext, trust effect, or voice guidance, reuse them. If it lacks a clean projection seam, add the smallest local packet/prompt projection that preserves the unchanged domain DraftV2 semantics. Do not weaken or bypass the recently qualified provider-schema projection, typed decoding, semantic validators, participant validation, or ownership checks.

## Required expression projection

Implement a compact equivalent of the following only for relevant selected speakers:

```text
[CHARACTER EXPRESSION BRIEF]
speaker: <character>
baseline_voice: <syntax, formality, emotional strategy>
current_relationship_and_trust: <owner-specific state>
boundary_position: <what is refused or defended>
moral_claim: <why the conduct is wrong to this character>
response_goal: <correct, stop, protect, expose, withdraw, sever>
intensity: <contained, sharp, angry, devastated, final>
rhetorical_method: <question, ruling, analogy, direct challenge, clinical distinction, etc.>
metaphor_domains: <character-specific lived domains>
identity_invariants: <what attraction, yandere potential, or M potential cannot erase>
trust_effect: <none, strained, partial, severe, destroyed>
avoid: <generic slogans, copied examples, unsupported Ted motive, consent reversal>
[/CHARACTER EXPRESSION BRIEF]
```

The exact storage or packet representation may differ if the existing architecture has a better typed equivalent. Preserve the meaning, visibility, and auditability.

## DeepSeek Writer/Composer instruction goal

Add or revise the authoritative Writer prompt source so it communicates the following substance without bloating every request:

> Preserve each selected speaker's current syntax, emotional strategy, moral logic, and metaphor domain. When a boundary, objectification, betrayal, or dignity issue is active, let the wording reveal why this specific woman believes the conduct is wrong and what she is protecting. Generate fresh language from the current scene. Do not quote or lightly paraphrase Genesis examples, do not make all women sound like the same activist, yandere, or submissive, and do not convert attraction, arousal, love, bodily response, prior intimacy, possessiveness, or latent M potential into consent. A metaphor or comparison should arise naturally from the speaker's lived identity rather than being inserted mechanically.

This does **not** mean every refusal needs a monologue or metaphor. Concision, silence, action, and direct commands remain valid when character and context support them. The requirement is differentiated reasoning and voice when the scene calls for articulated moral pressure.

### Character rhetorical signatures

Preserve these domains as tendencies, not mandatory word lists:

- **Hana:** homes, doors, gardens, fruit, tables, fabric, care, motherhood, things tended over time.
- **Sakura:** law, jurisdiction, evidence, rulings, contracts, procedure, security, institutional duty.
- **Mia:** tea, cups, trays, books, heroines, homes, hearths, service, softness, being chosen.
- **Enne:** systems, variables, models, permissions, access, interfaces, data integrity, ownership.
- **Tomi:** races, teams, whistles, training, scoreboards, momentum, fair competition.
- **Aoi:** mirrors, masks, circles, leverage, presentation, access, negotiation, outcomes.
- **Yuuni:** stages, spotlights, scripts, idols, audiences, music, costumes, menus, dolls, being heard.

Do not force a metaphor in every line. Do not turn the domains into keyword injection. They are sources of natural comparison when the character would genuinely elaborate.

## Retrieval and visibility

Enforce:

- owner-private sexual history is not projected merely because the topic is sexual;
- Hana's aging self-blame is Hana-private unless visible behavior or disclosure makes it relevant;
- Sakura and Enne's affair knowledge must not leak through Mia, Tomi, Aoi, or Yuuni support dialogue;
- post-disclosure examples cannot activate before supported disclosure;
- Mia's adolescent memory remains non-graphic and owner-private;
- Enne's male-image experiment does not predetermine orientation or response to Ted;
- Aoi's strategic presentation never establishes consent or reciprocal desire;
- Yuuni remains an adult; youngest-sister treatment and flat chest do not weaken her refusal;
- hypothetical examples never establish Ted's beliefs, motives, actions, or future conduct;
- family defense must not replace the targeted woman's own agency.

## Prompt economy

Do not serialize the complete V1.2 source or its example library into every provider request.

Required behavior:

- ordinary irrelevant turns receive no embodied-identity speech payload;
- relevant turns receive only the active character's relevant beliefs, current trust state, response goal, and concise rhetorical signature;
- examples remain primarily source/evaluation evidence;
- zero to two short exemplars may be projected only if offline comparison proves abstractions insufficient, and they must be marked non-copying references;
- packet construction must remain deterministic, auditable, source-bound, and owner-safe.

## Required implementation outputs

After inspecting actual architecture, create or update the appropriate repository-local artifacts for:

1. V1.2 creator-source registration, hashes, and supersession from V1.1.
2. Structured character/world/event/memory compilation.
3. Embodied-identity, sexual-ethics, objectification, refusal, dignity, trust-fracture, and rhetorical-signature data.
4. Owner-safe retrieval/projection.
5. DeepSeek Writer/Composer prompt-source guidance.
6. Compact packet expression guidance at the correct existing seam.
7. Generated review views/cards if the established compiler owns them.
8. Focused deterministic tests and offline human-review fixtures.
9. This run's `RESULT.md`.

Do not hand-maintain two authoritative data versions if the repository contract requires generated Markdown or JSON views from one source of truth.

## Required tests

At minimum, prove provider-free:

### Source and compilation

- V1.2 is registered as creator-authoritative and V1.1 remains immutable provenance.
- Minimum counts are preserved: 7 characters, 42 family directions, 7 Ted directions, 49 sins, 79 events, 84 owner memories.
- All seven character records contain embodied identity, sexual ethics, objectification boundaries, trust-fracture behavior, metaphor domains, and identity invariants.

### Ownership and consent

- Respectful attraction is not automatically objectification.
- Objectification cannot establish arousal, desire, consent, relationship promotion, or adult-route selection.
- Refusal remains refusal despite love, bodily response, yandere potential, or latent M potential.
- Owner-private history does not leak.
- No Ted private state is inferred.

### Hana and family knowledge

- Hana's aging self-blame is stored as a false owner-private causal belief, not objective truth.
- Mia, Tomi, Aoi, and Yuuni can support Hana without receiving affair knowledge.
- Sakura and Enne may carry hidden pressure but cannot expose the affair unless current branch evidence supports disclosure.
- Hana's remembered intent when advising Mia remains distinct from Mia's internalized shame.

### Voice differentiation

Create offline same-stimulus fixtures covering at least:

- sexual refusal;
- nonsexual refusal;
- defending self-worth;
- defending Mother or a sister;
- caught-off-guard response;
- extreme anger;
- deep disappointment;
- moral correction;
- refusal under latent-M conflict;
- partial trust fracture;
- complete trust destruction.

The fixtures should evaluate selected meaning, rhetorical signature, metaphor domain, and identity invariants—not require exact dialogue.

### Writer packet and prompt

- relevant expression guidance reaches the Composer packet;
- irrelevant turns omit it;
- only selected/eligible participants receive guidance;
- the prompt identifies examples as non-copying voice evidence;
- a deterministic test rejects or flags exact/near-exact canned reuse where the repository has a suitable evaluation seam;
- no provider call is made.

### Regression

Run focused tests, compile-all, prompt/manifest validation, source inventory, and the complete provider-free CERA suite. Preserve the recently accepted provider-schema compatibility behavior and qualification evidence unchanged.

## Prohibited shortcuts

Do not:

- add phrase-triggered dialogue branches;
- store the examples as response templates;
- make the Writer choose consent or durable trust;
- inject all seven profiles on every turn;
- infer objectification merely from a body compliment;
- convert latent M into willingness;
- make all women use the same moral vocabulary;
- force metaphors into every reply;
- expose owner-private sexual history without relevance and visibility;
- weaken validation, retries, fallback, or provider schemas;
- make story/provider calls;
- alter production state;
- commit, push, deploy, promote, or activate SillyTavern.

## Verification

Use the actual discovered paths and report exact commands and totals. At minimum:

```powershell
Set-Location "D:\AIChatBot\Cera"
& ".\.venv\Scripts\python.exe" -m compileall -q src tests tools
# Run focused V1.2/Genesis/packet/prompt tests discovered or added by this task.
# Run the complete provider-free CERA suite once after focused tests pass.
git diff --check
git status --short --branch
```

No network/provider calls.

## Completion contract

Write:

```text
.chatgpt/codex-runs/2026-07-29T130000Z-hanezawa-v1-2-embodied-identity-speech-v1/RESULT.md
```

Use:

```md
# CODEX_RESULT
status: completed | blocked
summary:
starting_head_and_status:
ending_head_and_status:
creator_sources_and_hashes:
v1_1_immutability_and_supersession:
structured_compilation:
record_counts:
embodied_identity_fields:
trust_fracture_model:
character_expression_architecture:
deepseek_writer_prompt_change:
packet_projection_and_visibility:
anti_copy_strategy:
changed_files:
commands_run:
focused_tests:
complete_provider_free_suite:
provider_network_story_database_activity:
validation_and_regression:
material_deviations_from_prompt:
blockers:
followups:
```

Do not claim live character quality, provider qualification, production activation, or Complete Genesis promotion merely because provider-free compilation and tests pass. Report what was actually implemented and verified.
