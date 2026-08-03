# CERA Runtime Model V3 Validator Schema Surface Correction Design

Checkpoint: `2026-08-03-cera-runtime-model-v3-validator-schema-surface-v1-001`

Queue: `0047`

Provider calls authorized for this correction: `0` Codex, `0` DeepSeek.

## Reconciled starting state

- Current source: Git `3b9b07c935ee8c8b5c08629cd570bb520135a1d5`, tree `20a1fcf75c8216366d42c88d9d4cb12c4b12b71f`, tracked-clean.
- Last accepted Stage 4 source: Git `4a691edd4748130963171d4f8cc089b17ca130e5`, tree `67fecb2b510d0dade84237eb0527432453d2dfe0`.
- Prompt-v12 commit `4d72e2e4d59ca4f930332fd667eb79d3b2dcd19a` and prompt-v13 commit `3b9b07c935ee8c8b5c08629cd570bb520135a1d5` are preserved intermediate commits, not accepted fixes.
- Terminal Queue 0046 result SHA-256: `4aecff39f19585636885b58e3155141cd3f665f428fb73bafede69449795a9dd`.
- Terminal report SHA-256: `6abf8c3242475fbee9db949a1c0df4f9d3afad91e16477236b31b526d891ebf8`.
- Failed-attempt SHA-256: `96377fc80079d6d6dc590060524c4c22013058e605d056a51b7705c1fd85bfdb`.
- Raw provider-result SHA-256: `53d08fffb523da172a5e4e097a50c4b866f1ed64e711c483baaa8849a827a96e`.
- Original Stage 4B Writer result SHA-256: `1a18a47eb4a9a6ad523f8d651d6450aa637adf39f9a9a5d207dfd70e1ad52abd`; it records `5/5` first-attempt passes.

## Writer evidence binding

The correction does not change the prose-only Writer boundary. Relative to accepted Stage 4A:

- `src/cera/continuous/provider.py` was unchanged before this correction (`c71de8516f64e668abbff6d4cfe8a07640d551df` at both revisions).
- `src/cera/providers/deepseek.py` was unchanged (`37df1e53829e45ced40fc49afe1c2a741930e623` at both revisions).
- The only existing `prompting.py` changes are Validator-only v12/v13 instructions. The DeepSeek Writer request, Writer DTO, adapter `cera.continuous_deepseek_adapter.v8`, prompt `cera.scene_writer_prompt.v1`, model route, and transport remain unchanged.

Therefore fresh Stage 4 V2 may bind the immutable original Writer result instead of recalling DeepSeek for Stage 4B, subject to a final post-correction Writer-surface hash/test check.

## Root cause

`FinalFieldScopeV1.field_name` is annotated as `str`, so `continuous_semantic_validator_draft_json_schema()` and its OpenAI projection expose only `{\"type\":\"string\"}`. Python independently accepts exactly five names. The provider returned schema-valid arbitrary names and the Python DTO correctly rejected them.

## Focused design

1. Add one provider-neutral `FinalFieldName` `StrEnum` containing exactly:
   - `realized_event`
   - `valid_deepseek_additions`
   - `knowledge_changes`
   - `material_changes`
   - `resulting_state`
2. Type `FinalFieldScopeV1.field_name` and the two Python-derived source-field references with this enum. Direct construction will normalize valid string values to the enum and reject all other values; strict mapping decode already decodes enums before dataclass construction.
3. Replace duplicated Python set literals and required-scope literals with enum members so the DTO, compiler, and provider schema cannot drift.
4. Let the existing dataclass schema generator emit the exact enum automatically. Do not create a generalized schema framework or special-case the provider projection.
5. Advance the active Semantic Validator draft schema identity from v1 to v2 and the Validator adapter identity from v9 to v10 because submitted provider schema bytes change.
6. Retain the v13 local-key and final-item role guidance because it exactly agrees with the existing Python `_KEY` and role-ledger contracts. This guidance does not replace the new enum enforcement.
7. Leave historical Validator/Composer schemas, frozen campaign evidence, Writer DTO/prompt/adapter/route/transport, Reader, Planner, and provider projection logic unchanged.

## Adjacent-surface audit boundary

The directly nested active Validator enums (`visibility`, role/semantic/task/status enums, operation kinds, and review enums) are already emitted as closed enums by the existing generator. Stable identity strings are intentionally not finite enums. Local keys are bounded by the existing Python regex and v13 instruction; they are not a five-value field-name vocabulary and are outside this exact enum correction. No other directly adjacent finite-string set was found exposed as an unrestricted string.

## Required focused proof

- Exact enum at every provider-neutral `FinalFieldScopeV1.field_name` path.
- Exact enum retained at every OpenAI-projected path.
- Equality between schema enum and `FinalFieldName` values.
- DTO decode and compiler success for all five values.
- Local JSON Schema and independent Python injection rejection for arbitrary values including `teacup_position` and `tea_question`.
- Active OpenAI schema preflight.
- Historical schema generation/reader compatibility.
- Writer schema remains exactly `schema_version` plus `story_text` and its hash remains bound.
- Focused affected tests, compilation, diff against accepted Stage 4A, and unchanged protected effects.

No source edit preceded this design record.
