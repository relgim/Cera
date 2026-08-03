# Runtime Model V3 Validator final-stop checkpoint

Status: `passed_provider_free_checkpoint`

The distinct Stage 4 V2 provider-wire defect is closed without weakening Python validation. The active Validator provider schema no longer asks the model to generate an independent `final_stop_state`; Python derives that domain field from the last accepted sequence item's `resulting_state`. The provider therefore cannot produce two schema-valid but unequal copies of the same fact.

The active identities are now `cera.continuous_semantic_validator_draft.v3` and `cera.continuous_validator_adapter.v11`. Historical V1 and V2 readers remain intact. The five-value `FinalFieldName` enum remains closed. The active and OpenAI-projected schemas are identical, omit `final_stop_state`, and have SHA-256 `623978e4742f9c609d8b73f67ada21c8d49c55e44304a80cb5f6bde8078b1c30`.

Provider-free gates passed: compilation, schema projection, drift and historical-reader checks, `git diff --check`, and 159/159 focused tests. The complete suite ran 979 tests: 972 passed, 3 skipped, and the same four cases were unavailable because this execution checkout lacks its own venv plus two historical runtime/evidence prerequisites. The correction changes none of those tests or launchers.

The failed V2 identity is immutable and non-reusable. It spent one Codex/Sol-medium call and zero DeepSeek calls. Its failed-attempt, result, report, and call-ledger evidence remain preserved. The next fresh identity is `2026-08-03-cera-runtime-model-v3-live-qualification-v2-correction-001`.

The Writer DTO, prompt, adapter, model route, transport, and original 5/5 first-attempt result remain unchanged and reusable. No provider call occurred during this correction. No route, production story/database, installed SillyTavern, service, deployment, remote, or protected-evidence effect occurred.
