# Provider-Schema Compatibility Result

**Status:** provider-free implementation and advisory review complete; live probe passed; conditional v4 is separate terminal evidence  
**Authorization:** D-119  
**Repository:** `D:\AIChatBot\Cera`

## Outcome

CERA now separates authoritative domain schemas from versioned provider
transport projections. The Python dataclasses and their semantic validators
were not weakened. Every Codex or DeepSeek response shape is inventoried,
projected for its provider dialect, recursively checked before dispatch, and
decoded through the authoritative Python contract after return.

The correction changes no v1, v2, or v3 qualification artifact. It makes no
story, Genesis, production, product, publication, promotion, or deployment
write.

## Provider profiles

| Profile | Provider behavior | CERA behavior |
|---|---|---|
| `openai_structured_output_2026_07_v1` | Strict JSON Schema subset; closed object root, all object fields required, nullable fields represented as unions, supported `anyOf`; no `oneOf` | Remove annotation-only `$schema`, project `oneOf` to disjoint `anyOf`, add explicit primitive types where needed, recursively reject unsupported/unknown keywords and provider budget violations before dispatch |
| `deepseek_json_object_prompt_2026_07_v1` | JSON-object mode, with schema semantics communicated in the prompt rather than enforced as strict response JSON Schema | Project a closed portable prompt schema, recursively lint it, include it in the hash-bound Composer packet, then perform strict typed and semantic Python validation after decoding |

OpenAI profile facts were checked against the official Structured Outputs
guide. DeepSeek profile facts were checked against its official JSON Output
guide. These provider projections are transport constraints, never CERA story
authority.

## Active schema inventory

| Boundary | Dialect | Authoritative SHA-256 | Provider SHA-256 | Transformations |
|---|---|---|---|---:|
| `codex_reasoner_draft_v2` | OpenAI v1 | `a8f91952e8ef4c04098f681c05b3cfc8e5c2e4ea6516d989d1ccbbfd858f8c8d` | `9c2545e678ff3a9ac5953920a79b090491f81888e6311c16bf3754467c2caede` | 1 `oneOf` to `anyOf` |
| `codex_transport_probe` | OpenAI v1 | `3a15b71f457b13355dc0283eb5403dd33ff47011b380422dfa08231c18634177` | same | none |
| `codex_mcp_bridge_probe` | OpenAI v1 | `cf533fa751a223e638e9e167bf3cebf2d4e66e10d27401a4635467ab0c379a9f` | same | none |
| `deepseek_composition_draft_v2` | DeepSeek JSON-object prompt v1 | `19af7e23693bdd54221dd2e152454dfc8ab846f77fd341e2f4979541c35527c6` | `d0273f79fe7e233845fadaad455b2f3b13a267499d700b14b9c0e685d85606b9` | prompt-schema normalization |

The AdultCraftNeed channel-owner alternatives remain disjoint after OpenAI
projection: `dialogue` and `inner_voice` require `character_id`; `narration`,
`sound_effect`, and `physiology` forbid it through closed-object validation.
The authoritative Python discriminated union and every cross-field validator
still run after provider decoding, so a simplified provider schema cannot
promote an invalid channel owner.

## Implementation

- `cera.providers.schema_dialects` owns dialect IDs, deterministic projection,
  recursive compatibility validation, inventory, and shared probe schemas.
- `CodexSDKTransport` projects and validates every supplied output schema
  before request hashing or external dispatch.
- `DeepSeekSceneComposerPort` includes the DeepSeek-profile prompt schema in
  packet/prompt v3. DeepSeek still uses JSON-object mode; Python remains the
  typed and semantic acceptance boundary.
- Reasoner and Composer adapter/packet/prompt identities advance to v3 where
  the provider-facing contract changed. Authoritative Reasoner draft v2,
  Composer draft v2, AdultCraftNeedV2, and Python domain schema versions remain
  unchanged because their meanings did not change.
- The minimal live probe is a separate explicit command. It uses the complete
  Reasoner draft schema, retains no prompt or raw output, refuses overwrite,
  makes one Sol-medium call, and requires authoritative typed decoding.

## Verification

```text
Focused compatibility and adapter suite: 56/56 passed
Complete provider-free repository suite: 291/291 passed
Complete-suite duration: 200.271 seconds
20-run fake-adapter acceptance: 20/20 committed, 20/20 zero-call replays
Source inventory: 107 Python package files, required packages present
Live provider calls during implementation/tests: 0
Story/production writes: 0
Qualification evidence changed: 0
```

The tests cover complete schema inventory, recursive provider-dialect
preflight, schema/domain differential behavior, channel-owner mutation,
projection equivalence, optional-field rejection, malformed provider output,
unknown/unsupported keywords, and the existing broad malformed DTO, privacy,
branch, restart, retrieval, participant, consent, and publication boundaries.

## Remaining gate

After ChatGPT Pro's advisory review, the creator authorization allows exactly
one non-story Sol-medium schema-acceptance probe. A provider-schema rejection
ends the work before story cases. Only a successful provider and authoritative
typed acceptance permits one fresh immutable five-case batch. Every case
remains one-shot, with no retry, fallback, story commit, promotion, product
activation, production binding, publication, external handler, or deployment.

## Advisory review

ChatGPT Pro returned exactly:

```text
CERA_PROVIDER_SCHEMA_COMPATIBILITY_ACCEPTED
```

Pro required no correction before the probe and explicitly disclosed that the
current D-drive files were unavailable for direct line-by-line inspection.
Codex independently compared the advisory reasoning with the implementation,
provider documentation, schema inventory, projection-equivalence evidence,
and final test result and found it consistent. Pro's broader ideal receipt
list includes provider terminal/model metadata that the current Codex SDK does
not always expose; CERA retains the available safe request/output/schema
hashes, request-bound model and effort, provider receipt, decode outcome,
latency/usage, one-call count, and zero-write evidence without inventing
provider-verified fields.

## Live schema probe result

The D-119 non-story Sol-medium probe passed:

```text
Provider schema accepted: true
Authoritative domain accepted: true
External provider calls: 1
Automatic retries: 0
Story authority writes: 0
Provider schema SHA-256: 9c2545e678ff3a9ac5953920a79b090491f81888e6311c16bf3754467c2caede
Output SHA-256: bd369945a4234bdb337fa2c172992932b6c0307c2b26012d0a1fa647f4c0661f
Duration: 4834 ms
```

The immutable privacy-safe evidence is under
`evaluation/evidence/codex_schema_acceptance_2026-07-29_v1/summary.json`.
This passes the compatibility gate only; the later v4 story-role result remains
separate terminal evidence.
