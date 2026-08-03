# Validator Nested Identity Correction Design

Checkpoint: `2026-08-03-cera-runtime-model-v3-validator-nested-identity-v1-001`

Authority: Queue 0048, provider-free correction phase

## Frozen baseline

- Git commit: `80ffd5591f80bbce468ff55b5d88fa4ec87b4864`
- Git tree: `6d640fbaf5ea4bccbc1a0766fa8911ed221c114d`
- Queue 0047 terminal result SHA-256: `97f2fcb89a634413fd56eebe4a7818e80b48610779a91290874ff928b9b4d5fc`
- Queue 0047 terminal report SHA-256: `b45bc0d5a45e36780d7c981724ea785571d56a7ffac2b032ddafddb9e002015b`
- Queue 0047 failed-attempt SHA-256: `d13c67767fcf1c70f92d3758f4818d1e4c2d06baf3d34aa8f9fba849272da652`
- Queue 0047 call-ledger SHA-256: `b048b810c27dd251a1b4b7c0280a8f58355a7d8d478df8127f0a63a8f6635a6b`
- Provider calls during this correction so far: Codex `0`; DeepSeek `0`; all others `0`.

## Execution-checkout prerequisite closure

The four Queue 0048 prerequisite tests were run in `D:\CP25\source` with temporary, read-only access to the authoritative manager-repository prerequisites. Three directory junctions and one read-only database copy were used; no authoritative fixture was edited.

The first combined run reported `4/4 passed` in `70.969s`, but subsequent
inspection proved that the borrowed virtual environment's editable install had
resolved `cera` from `D:\AIChatBot\Cera\src` rather than the execution checkout.
That run is retained as environment diagnostics only and is not accepted as
execution-source evidence. The tests must be rerun with
`PYTHONPATH=D:\CP25\source\src`, and the imported `cera.__file__` must be recorded,
before the prerequisite gate can be classified as passed.

The corrected run explicitly recorded
`IMPORTED_CERA=D:\CP25\source\src\cera\__init__.py` and passed all four exact
tests in `62.527s` (`63.204s` measured command duration). This is the accepted
prerequisite result. The temporary prerequisites were removed again, and the
authoritative manager database retained the expected SHA-256.

The temporary junctions and database copy were removed immediately after the run. The authoritative manager database remains present with SHA-256 `bfbf23eb2fa7199fad38e8fb3f1ae0d6b547467f17ed68e84b7115275a2fc555`.

## Root cause

The active provider-facing nested final-sequence wire exposes `schema_version` as an unrestricted string. Python subsequently requires the canonical `FinalSequenceV1.SCHEMA_VERSION` value, `cera.complete_final_sequence.v6`. The provider contract therefore permits a value that the Python DTO rejects.

## Correction design

1. Preserve all historical provider wires and readers, including Validator drafts V1-V3 and `ProviderFinalSequenceDraftV1`.
2. Add a fresh `ProviderFinalSequenceDraftV2` that omits `schema_version` entirely from provider-owned data.
3. Its Python compiler injects `FinalSequenceV1.SCHEMA_VERSION`; the provider can no longer author or vary this identity.
4. Add a fresh active Validator wire version using `ProviderFinalSequenceDraftV2`; version the active adapter and compatibility identity accordingly.
5. Keep the closed Python DTO validation unchanged. Do not broaden accepted nested versions.
6. Recursively audit the provider-neutral schema and final OpenAI/Codex projected schema. Every property named `schema_version` must either be absent because Python injects it or contain an exact `const`; unrestricted nested identities are a test failure.
7. Bind every remaining provider-visible version constant to the corresponding Python-owned canonical value in tests.
8. Add an optional Validator raw-result observer at the adapter boundary. It receives a defensive copy of benign parsed provider JSON immediately before DTO decoding. The default is no retention; only the live qualification harness supplies an evidence writer.
9. The live harness writes the raw parsed Validator result atomically, records its SHA-256, and preserves it even when DTO decoding fails. It must not retain protected prose or alter production persistence.
10. Prove focused unit/contract tests first, then run the complete provider-free suite from the frozen candidate using the same temporary-prerequisite method. Remove all temporary prerequisites after testing.

## Invariants

- No provider call occurs before this checkpoint is committed and frozen.
- No historical schema or failed campaign evidence is rewritten.
- No active/default route, story database, installed SillyTavern, service, network, deployment, merge, remote, or push effect occurs.
- Raw evidence capture is qualification-only, non-authoritative, and cannot affect DTO acceptance.
