# Active provider-output field ownership inventory

This inventory covers the active Planner, Writer, Semantic Validator, and Reader provider-neutral schemas and their final Codex/OpenAI projections. Repeated paths under union branches have the same classification.

| Required field family | Classification | Enforcement |
|---|---|---|
| Every `schema_version` | supplied immutable constant | Every active occurrence is a closed string `const` before and after provider projection. |
| Planner `provisional` and `accepted_turn_id` | supplied immutable constant | `provisional` is `true`; current `accepted_turn_id` is `null`. Python owns the later accepted identity. |
| World, branch, turn, candidate, protected-user, accepted-turn, and supplied record identities | exact supplied value | The provider may echo only identities visible in the request; Python cross-checks them against typed request/world/candidate custody before acceptance. |
| Provider-local package, sequence, event, verdict, segment, item, adjudication, directive, and reason/issue keys | semantic label or semantic grouping choice | Closed DTO syntax, uniqueness, references, branch rules, traceability, and local-key validation apply. These labels create no durable authority by themselves. |
| Planner beats, participant roles, intent/causality/private/material fields, evidence references, and stop direction | genuine semantic model judgment | Closed enums and role ledgers plus evidence, protected-user, and runtime validation bound the choices. |
| Writer `story_text` | creative provider output | The Writer schema contains only constant `schema_version` and `story_text`; Python constructs the candidate identity and mechanical envelope. |
| Validator decision branch, semantic statuses, story segment kinds/roles/exact text, final-field meanings, visibility, knowledge ownership, review diagnostics, event meaning, and persistence destination | genuine semantic model judgment | Visible Writer/request/evidence input supports the selections; Python validates complete coverage, exact bytes, roles, traceability, writable paths, and authority. |
| Validator and Reader `output_start`/`output_end` | genuine semantic subspan selection | The model chooses boundaries from visible immutable text; Python proves integer type, nonempty bounds, cited-segment containment where applicable, and exact bytes. |
| Validator story-segment `exact_text` | exact visible-text selection | It is necessary semantic evidence, not a cryptographic calculation. Python requires equality with the typed immutable Writer slice. |
| `persistence_policy_sha256` | supplied immutable constant | The only active provider-output `*_sha256`; both neutral and projected schemas require the exact Python policy `const`. |
| Adjudication `exact_text_sha256` | deterministic Python derivation | Omitted and rejected on the active provider wire; derived from the validated typed Writer subspan. |
| Reader `story_text_sha256` and issue `exact_text_sha256` | deterministic Python derivation | Omitted and rejected on the active provider wire; derived from the typed immutable Writer text and validated issue spans. |
| Persistence `expected_prior_value_sha256` | deterministic Python derivation | Omitted and rejected on the active provider wire; Python injects `null` for add or hashes the exact bounded ACTIVE JSON-pointer value for replace before canonical decode. |
| Final sequence schema identity, accepted-turn identity, and final stop-state copy | Python-owned nested identity/derivation | The active draft preserves the existing Python compiler: nested sequence identity and the exact last resulting state are not open provider bookkeeping. |
| Candidate, mechanical-envelope, provider receipt, call-ledger, event participant, edit-operation, created-field, acceptance, memory, and commit hashes/identities | unnecessary provider output | Absent from the active role output schemas; Python constructs them from typed source and validated semantics. |

Recursive tests walk all four active neutral and projected schemas. No required active cryptographic calculation remains assigned to a model, and no required field remains unclassified under these exhaustive families.
