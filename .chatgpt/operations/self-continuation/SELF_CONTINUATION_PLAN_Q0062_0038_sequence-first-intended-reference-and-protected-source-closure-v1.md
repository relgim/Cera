# CERA Codex Self-Continuation Plan

plan_schema_version: `cera.codex_self_continuation_plan.v1`  
plan_id: `self-continuation:q0062:0038:sequence-first-intended-reference-and-protected-source-closure-v1`  
queue_revision: `0062`  
created_at_utc: `2026-08-06T06:51:44.9835618Z`  
status: `plan_written_proceeding`  
execution_repository: `D:\CP25\source`  
execution_git_sha: `50b6b9549e1a902bfbd8838b06de4e09b3cab23d`  
execution_tree_sha: `8fdf9890aaf466bb5b675ed42cc75053bd33af7c`

## Trigger

- current stage/task: `Queue 0062 Stage 3 V2 first Planner turn`
- failed identity: `2026-08-06-cera-sequence-first-stage3-live-v2`
- failure result: `.chatgpt/pf/q0062_sequence_first_stage3_live_v2/STAGE3_RESULT.json`
- failure result SHA-256: `b71732c434a8231559800dbb1aa7ad41137bdaf6e521ffb1a6ce3cfd9111c61e`
- provider ledger: `.chatgpt/pf/q0062_sequence_first_stage3_live_v2/PROVIDER_CALL_LEDGER.jsonl`
- provider ledger SHA-256: `6876ac0d81f0b87f7420247252d7316072c326a73e0ae14165e35eaaf6bd2f5e`
- exact archived Planner rollout SHA-256: `186d90b2e02e53fc8c3b3e8eb26e25298732f9de9bb5ac2123804a5f9a18fc3f`
- provider debit: `1 Codex/Sol, 0 DeepSeek`
- accepted turns: `0`
- planner thread terminalized: `true`

## Classification

- issue class: `A+B`
- exact blocker: `The shared intended/realized sequence-item provider schema allowed non-empty planner_item_keys on Planner output, while Python correctly rejects those references on intended items.`
- adjacent exact authority defect: `The same live output attached protected-user exact source to a Hana-owned item even though Queue 0062 authority reserves protected-user claims and quotes for Ted-owned assertions.`
- genuine creator decision required: `false`
- reason existing authority resolves desired behavior: `Queue 0062 requires provider/DTO integrity and exact protected-user source custody, and permits the smallest provider-free correction under a fresh identity.`

## Current product objective

`Make intended and realized structured surfaces express their different reference roles while keeping protected-user authority local to Ted-owned assertions.`

## Evidence and root-cause hypothesis

`The exact V2 Planner output placed hana_answers_dinner_question in the stopping item's planner_item_keys and copied the full exact current source into protected_user_exact_quotes on a Hana-owned dialogue item. The first was provider-schema compliant but DTO-invalid; the second passed the current Python checks despite violating the controlling protected-source contract.`

## Smallest suitable correction

- retained behavior: `all sequence semantics, responder derivation, presence, exact-source availability, Writer wire, Validator/Reader roles, persistence, HTTP, review, and acceptance behavior`
- provider schema correction: `parameterize sequence item/draft schemas by intended versus realized role; intended planner_item_keys remains required for strict output but has maxItems 0; realized Validator items retain local-key references`
- Python authority correction: `reject protected_user_claim_keys or protected_user_exact_quotes on any item not owned by character:ted`
- prompt correction: `tell the Planner to leave planner_item_keys empty, use source:current as evidence for NPC reactions grounded in the current message, and use protected claims/quotes only on Ted-owned items`
- compatibility correction: `version the Planner provider prompt/adapter/route identity`
- explicitly excluded changes: `provider calls before freeze, output repair, normalization, Python validation broadening, retries, fallbacks, model changes, production route, database, installed SillyTavern, deployment, merge, push`

## Provider and attempt boundary

- provider calls before checkpoint: `0`
- remaining creator balance: `600 Codex/Sol, 719 DeepSeek`
- remaining original Stage 3 ceiling: `8 Codex/Sol, 6 DeepSeek`
- fresh V3 maximum: `8 Codex/Sol and 4 DeepSeek through at most two independent Writer attempts per turn`

## Verification plan

1. `Schema test proves intended planner_item_keys has maxItems 0 and realized planner_item_keys retains the local-key pattern without maxItems 0.`
2. `Contract tests prove non-Ted protected claims/quotes are rejected and exact Ted-owned source remains accepted.`
3. `Focused sequence-first provider/runtime/HTTP tests.`
4. `Complete repository suite at the corrected source freeze.`
5. `Commit provider-free source and result evidence, then use a fresh Stage 3 V3 identity.`

## Success criteria

`Planner output cannot contain non-empty realized references under its submitted schema; Validator realized coverage remains expressible; protected source cannot authorize a non-Ted-owned assertion; all focused and complete gates pass with zero correction calls.`

## Stop and escalation conditions

`Stop before another provider call if gates fail, authority changes, accounting becomes ambiguous, or the fresh V3 repeats either corrected contract defect.`

## Remaining queue work after this correction

`Fresh Stage 3 V3 two-turn canary -> two isolated SillyTavern runs -> mandatory twenty-accepted-turn campaign.`
