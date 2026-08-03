# Runtime Model V3 Validator nested-identity checkpoint

Status: `passed_provider_free_checkpoint`

The active Semantic Validator contract now removes the nested final-sequence
`schema_version` from provider ownership. `ProviderFinalSequenceDraftV2`
contains only semantic fields; Python injects the exact
`cera.complete_final_sequence.v6` identity and derives the final stop state.
An attempted provider-authored nested version is rejected as an extra field.
Historical provider wire V1 and Semantic Validator readers V1-V3 remain
available for immutable evidence.

The active identities are now
`cera.continuous_semantic_validator_draft.v4`,
`cera.continuous_validator_adapter.v12`, and unchanged prompt
`cera.continuous_validator_prompt.v13`. The provider-neutral and final
Codex/OpenAI-projected schemas both hash to
`5d5e902d815a65feeeaf4db39c440462ff9704e7ac777ca87d90250aaa4f7a93`.
All eight remaining `schema_version` paths are exact Python-derived constants;
there are zero unrestricted paths. The five-value final-field enum remains
unchanged and closed.

The qualification-only raw-result hook now freezes one canonical
`RAW_PROVIDER_RESULT.json` artifact before DTO decoding, records the exact
artifact SHA-256, refuses overwrite, and leaves the call ledger in the explicit
post-validation-failure terminal state when decoding fails. It is opt-in and
does not enable production prose retention.

Provider-free validation passed:

- focused schema, DTO, projection, history, raw-custody, and actual-port tests:
  14/14;
- the four formerly blocked prerequisite tests, with
  `D:\CP25\source\src\cera\__init__.py` explicitly imported: 4/4 in 62.527s;
- complete suite: 982 tests, 979 passed, 3 expected platform skips, 0 failed,
  in 1309.307s;
- compilation, typed OpenAI schema preflight, and `git diff --check`: passed.

The governed prerequisites were temporary. All three junctions and the
read-only database copy were removed after both runs. The authoritative
database remains hash-identical, passes `integrity_check`, and has zero foreign
key findings.

The implementation diff against `80ffd5591f80bbce468ff55b5d88fa4ec87b4864`
is 34,017 bytes with SHA-256
`7820c855b0bb4a955ce3af1766786e85fa15ae856502542192e62bc425fa314f`.
The staged source-candidate tree is
`7ea1c1d3203766d7215564ed0d65443072b5ea8a`.

The Writer DTO, prompt, adapter, route, transport, and schema remain unchanged;
the Writer schema hash is
`dec9386e5a4b0fd3c9c9f48cd4826a14daf17009970c2be0d5bfa336ba8a717d`.
The original immutable 5/5 first-attempt Stage 4B result remains bound without
new Writer calls.

Provider calls: 0 Codex, 0 DeepSeek. Active route, production story/database,
installed SillyTavern, services, deployment, remotes, and protected evidence
remain unchanged. Queue 0048 therefore opens the fresh Stage 4 V3 program only
after this checkpoint is locally committed.
