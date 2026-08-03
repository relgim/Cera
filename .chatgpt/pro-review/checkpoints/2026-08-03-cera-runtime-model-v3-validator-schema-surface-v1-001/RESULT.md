# Runtime Model V3 Validator schema-surface checkpoint

Status: `passed_provider_free_checkpoint`

The active Semantic Validator contract is now closed at the provider boundary. `FinalFieldName` is the one Python vocabulary for the five accepted final fields, the provider-neutral schema exposes that exact enum, and the final Codex/OpenAI projection retains it unchanged. Python continues to reject arbitrary values independently.

Compatibility identities advanced to `cera.continuous_semantic_validator_draft.v2` and `cera.continuous_validator_adapter.v10`. The historical v1 reader remains available. The v12 and v13 prompt-only commits remain preserved intermediate history; neither is accepted independently, and this schema-backed descendant supersedes both.

The final implementation diff against accepted Stage 4A is 38,685 bytes with SHA-256 `74d18a3fb63971e16ec86fe021720372b70a7ddfea3fec59c81ed3624b8f5ab9`. The staged implementation source tree is `a96578564e8564281e87fad2b612e3a2706438c9`. The active and projected Validator schema SHA-256 is `38b0ec997c22e1a7c829f948ceb546f86516d8acafe1bdba0b945cbb997939c6`.

Provider-free gates passed: compilation, OpenAI typed preflight, diff checks, and 157/157 focused tests. A complete-suite diagnostic ran 977 tests: 970 passed, 3 skipped, and 4 could not execute because this `D:\CP25\source` checkout lacks its own venv and two historical runtime/evidence prerequisites. The four affected test/launcher files are byte-unchanged from accepted Stage 4A, so these are recorded as checkout-environment gaps rather than correction regressions; unrelated tests were not weakened or edited.

The prose-only Writer schema remains exactly `schema_version` plus `story_text`, hash `dec9386e5a4b0fd3c9c9f48cd4826a14daf17009970c2be0d5bfa336ba8a717d`. Its DTO, prompt, adapter, route, and transport are unchanged, so the original immutable Stage 4B 5/5 first-attempt result remains eligible for binding in Stage 4 V2.

Provider calls: 0 Codex, 0 DeepSeek. Active route, production story/database, installed SillyTavern, services, deployment, remotes, and protected evidence remain unchanged.
