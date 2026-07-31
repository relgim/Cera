# Modular Composer Compiler Scaffolding Result

**Authority:** D-159
**Status:** provider-free inactive scaffold complete; authoring and activation remain closed
**Date:** 2026-07-30

## Outcome

CERA now has the smallest provider-neutral seam needed to replace the current
monolithic Composer prompt later without changing it now. The new `cera.prompting`
package provides a hash-bound module registry, deterministic selector,
coverage-preserving Adult catalog bridge, compiler, and privacy-safe receipt.
Its state model deliberately contains only `disabled`, `fixture_only`, and
`shadow_only`; there is no active state or provider-dispatch capability.

The active route remains:

- adapter `cera.deepseek_scene_composer.v19`;
- prompt `cera.deepseek_scene_composer_prompt.v18`;
- packet `cera.deepseek_scene_composer_packet.v12`;
- Composer DTO `cera.deepseek_composition_draft.v6`;
- default model `deepseek-v4-flash`;
- static system SHA-256
  `01e2c7d2ae1021e0a1001f2eaa619960ae057a7db2b2e44c4c01912b2e388681`.

The active prompt text, application path, SillyTavern adapter, and human-test
world were not modified. The only existing prompt-manifest correction updates
a stale advisory compatibility binding from Composer prompt v6 to the actual
v18; the hash-bound character-expression source and active prompt bytes are
unchanged.

## Typed design

`AdultRenderingMode` is an explicit `off|on|ex` rendering preference. It is
independent of Adult route eligibility, consent/capacity, selected acts, cast,
causality, and `SceneDepthMode`. OFF rejects all Adult craft. ON and EX accept
only the exact ordered, receipt-bound output of the existing coverage-based
Adult selector. No second catalog or fixed fragment/example quota was added.

Python owns exact modes, eligibility, cast, topology validation, registry
lookup, local-path validation, module loading/hashing/order, conflicts, and the
selection receipt. A Reasoner may propose only bounded scene-function, tone,
floor-pattern, or interiority semantics. It cannot return module IDs, paths,
filenames, or raw prompt text.

The compiler supports stable authority, structured-output teaching, one Depth
profile, one Adult profile, one topology profile, one scene profile, selected
character/evidence material, variable craft fragments/examples, and exact
request obligations/schema. It binds the SequencePlan and selected-cast hashes.
EX cannot alter either binding.

Prompt bytes, diagnostic token estimates, module counts, selected fragment
counts, and example counts are telemetry only. There is no creative prompt
ceiling, automatic truncation, downgrade, or fixed count. Separate defensive
registry bounds prevent malformed repository data from reading more than 128
modules, 1 MiB per module, or 8 MiB total; these limits do not select or remove
creative content.

## Frozen Pro packet

The complete frozen authoring packet is under:

`.chatgpt/codex-runs/20260730-032157-modular-composer-scaffold-v1/CHATGPT_PRO_AUTHORING_PACKET`

It contains 52 hash-listed files, four full synthetic/noncanonical rendered
packets, all 12 Depth by Adult combinations, current prompt anatomy, the exact
compiler interface, sanitized assistant-only writing evidence, current schema
and prompt sources, the 82-fragment Adult catalog manifest, and Sera comparison
hashes/guidance. It contains no database, API key, secret, private user
transcript, or live provider prompt/output dump.

Hashes:

- packet manifest:
  `cd08ffb72a32e5ec49627ad056e52b35d0544002581a59ced2cc206b783a0bea`;
- source snapshot manifest:
  `b9e355bee9d1688404d9c89c18b646e9016c2ae2cc1de7d70342b9fe9a07aba4`;
- fixture registry manifest:
  `67d9c6b1d7479c7f658769cda17c34681ec6acc2bb668b31eecb1d084a55d275`;
- inactive registry JSON Schema:
  `67f2d696cf4a33dba6ba750ad6f3f723c967058d2aec0da5c42055a8d8f663d3`.

One read-only query against the non-production human-test SQLite database
retrieved two accepted assistant artifacts for the sanitized writing-evidence
document. No input transcript, database, raw provider response, or private
evidence was copied, and the database received zero writes.

## Verification

- modular compiler tests: 14/14 passed;
- focused cross-section: 99/99 passed in 42.833 seconds;
- Depth by Adult matrix: 12/12 assembled deterministically;
- representative packets: 4/4 rendered provider-free;
- complete repository suite: 464/464 passed in 194.358 seconds on the exact final tree;
- Python `compileall`: passed;
- JSON parsing, source inventory, manifest/hash, packet privacy, and static
  whitespace checks: passed after correction;
- provider calls: 0;
- story/database writes: 0;
- Adult route activations/publications: 0;
- retry, fallback, or Detailer additions: 0.

`D:\AIChatBot\Cera` is not a Git worktree, so a meaningful Git head/status and
`git diff --check` are unavailable. The result records this rather than
inventing Git evidence; repository-local compile, JSON, inventory, hash,
newline, and trailing-whitespace checks provide the available static evidence.

## Remaining gate

ChatGPT Pro may now draft writing-facing module content against the frozen
packet. That prose is not authoritative merely because it is drafted. Codex
must independently review and integrate it under a later explicit provider-free
authorization. Modular shadow/live activation, Flash qualification, Adult
publication, production binding, promotion, handler integration, and deployment
remain closed.
