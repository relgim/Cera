# CERA Codex Self-Continuation Plan

plan_schema_version: `cera.codex_self_continuation_plan.v1`  
plan_id: `self-continuation:q0062:0036:sequence-first-local-key-schema-surface-v1`  
queue_revision: `0062`  
created_at_utc: `2026-08-06T06:05:46.4815419Z`  
status: `plan_written_proceeding`  
execution_repository: `D:\CP25\source`  
execution_git_sha: `87fdad6b03d98e8063cf3dc51925a2465308b78b`  
execution_tree_sha: `8373ced51a10cf6bbcd70fa3b8765e085f23a110`

## Trigger

- current stage/task: `Queue 0062 Stage 3 bounded live two-turn canary, first Planner call`
- failed or terminal identity: `2026-08-06-cera-sequence-first-stage3-live-v1`
- failure/result path: `.chatgpt/pf/q0062_sequence_first_stage3_live_v1/STAGE3_RESULT.json`
- failure/result SHA-256: `655b9312119ab17cad04adee7e1cdc64c2d0ac2d6b2b67b2df845a3deec9069b`
- provider-call-ledger path and SHA-256: `.chatgpt/pf/q0062_sequence_first_stage3_live_v1/PROVIDER_CALL_LEDGER.jsonl`, `46bbd203414caeceb25886380a97744dec920f23d019fbbcf3a7cb6357a597cd`
- exact archived Planner rollout SHA-256: `fc8c5f3f1905ed7a101c03212342207329bd67dbf1c93c9e6787c3eb9a8035a8`
- protected-effects receipt/hash: `STAGE3_RESULT.json records no production, installed SillyTavern, or source-database effect; no candidate reached Writer or acceptance`
- manager authority check: `CURRENT.md remains Queue 0062; all mandatory authority was reread before diagnosis`
- manager response check: `No newer queue or repository manager response exists`

## Classification

- issue class: `A+B`
- exact blocker: `The Planner returned colon-prefixed item keys allowed by the submitted JSON schema, while Python's closed DTO requires [a-z][a-z0-9_]{0,95}; CERA rejected its own provider-compliant response after one Sol call.`
- genuine creator decision required: `false`
- reason existing authority resolves desired behavior: `Queue 0062 requires exact DTO/provider contract agreement and permits the smallest provider-free correction before a fresh Stage 3 identity.`

## Current product objective

`Make the model-visible structured schema and stable instructions expose the exact Python local-key grammar without broadening Python validation or repairing model output.`

## Evidence and root-cause hypothesis

`The archived output used item:hana_answers, item:mia_remains_backgrounded, and item:stop_for_ted. sequence_draft_json_schema declared item_key and related local-key references as unconstrained strings. The falsifiable root is schema/prompt under-specification, not Planner semantic reasoning.`

## Smallest suitable correction

- retained behavior: `all sequence-first role ownership, DTO closure, no hidden repair, no key normalization, exact source, Writer, Validator, Reader, persistence, HTTP, and acceptance behavior`
- proposed correction: `introduce one shared provider local-key JSON schema with pattern ^[a-z][a-z0-9_]{0,95}$; apply it to every provider-authored field Python validates with _key; state the grammar in the relevant stable instructions; version affected prompt/adapter identities`
- expected files/contracts: `src/cera/sequence_first/provider.py, prompting.py, focused schema tests`
- explicitly excluded changes: `Python key grammar, model role, model/effort, retries, fallbacks, output normalization, production route, database, installed SillyTavern, deployment, merge, push`

## Provider and attempt boundary

- provider calls before checkpoint: `0`
- provider debit from failed identity: `1 Codex/Sol, 0 DeepSeek`
- remaining creator balance before correction rerun: `601 Codex/Sol, 719 DeepSeek`
- authorized provider families/routes after checkpoint: `Queue 0062 frozen Stage 3 routes only`
- maximum calls for fresh rerun: `remaining Stage 3 ceiling of 9 Codex/Sol and 6 DeepSeek`
- retries/fallbacks/substitutions: `none except the existing maximum three independent DeepSeek Writer attempts per frozen brief`
- frozen inputs and fresh identities: `preserve failed V1; use a fresh V2 run, world, branch, sessions, ledger, workspaces, and result path`

## Verification plan

1. `Exact provider schema test proves all Python-local-key surfaces retain the regex and colon-prefixed examples are invalid.`
2. `Focused sequence-first provider/runtime/HTTP tests.`
3. `Complete repository suite at the corrected source freeze.`
4. `Commit provider-free correction and result evidence.`
5. `Run fresh Stage 3 V2 only after all gates pass.`

## Success criteria

`Provider schema and Python accept the same local-key set; arbitrary colon-prefixed keys are impossible under the submitted schema; no output repair is added; focused and complete suites pass; the fresh Stage 3 route remains within its reduced ceiling.`

## Stop and escalation conditions

`Stop before another provider call if focused/full gates fail, authority changes, accounting becomes ambiguous, or the same corrected local-key root recurs in the fresh V2 identity.`

## Manager alignment note

`This correction binds the provider surface to the already-authoritative Python contract. It does not tighten creative prose or alter the sequence-first architecture.`

## Remaining queue work after this correction

`Fresh Stage 3 V2 two-turn canary -> two isolated SillyTavern runs -> mandatory twenty-accepted-turn campaign.`
