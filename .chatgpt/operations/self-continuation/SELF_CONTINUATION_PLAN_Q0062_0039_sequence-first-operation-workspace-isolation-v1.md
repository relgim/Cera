# CERA Codex Self-Continuation Plan

plan_schema_version: `cera.codex_self_continuation_plan.v1`  
plan_id: `self-continuation:q0062:0039:sequence-first-operation-workspace-isolation-v1`  
queue_revision: `0062`  
created_at_utc: `2026-08-06T07:15:55.5745758Z`  
status: `plan_written_proceeding`  
execution_repository: `D:\CP25\source`  
execution_git_sha: `d90d38aadd394d50fe3f38d7d4f13fd96f2ca243`  
execution_tree_sha: `60057c43b437edf8cb45b6c716b0a76d6cd9a5a3`

## Trigger

- current stage/task: `Queue 0062 Stage 3 V3, second Writer candidate before Validator construction`
- failed identity: `2026-08-06-cera-sequence-first-stage3-live-v3`
- failure result: `.chatgpt/pf/q0062_sequence_first_stage3_live_v3/STAGE3_RESULT.json`
- failure result SHA-256: `221d56e02572c69a8b7b5b3aa056432f410b16f9de96e5e520a889b56739384e`
- provider ledger: `.chatgpt/pf/q0062_sequence_first_stage3_live_v3/PROVIDER_CALL_LEDGER.jsonl`
- provider ledger SHA-256: `600ce4ea8b52e9b705a76888c9ecc812a831b25dbf514bd30e7128e12196c00b`
- provider debit: `2 Codex/Sol, 2 DeepSeek`
- accepted turns: `0`
- planner thread terminalized: `true`

## Classification

- issue class: `A`
- exact blocker: `Sequence-first Codex adapters reuse one qualification workspace across operations. CodexSDKTransport requires an empty workspace, while each completed worker intentionally leaves a progress receipt. Validator attempt 2 therefore failed before dispatch because Validator attempt 1 residue remained.`
- broader proven exposure: `The persistent Planner would hit the same workspace collision on Turn 2, and the fresh Reader would hit it on a later candidate.`
- genuine creator decision required: `false`
- reason existing authority resolves desired behavior: `Queue 0062 requires persistent Planner thread identity, fresh Validator/Reader thread identity, separate workspaces, exact evidence preservation, and routine A/B self-continuation.`

## Current product objective

`Preserve provider thread semantics while giving every Codex operation a fresh immutable worker workspace whose receipts remain auditable.`

## Evidence and root-cause hypothesis

`V3 completed one Planner, one Validator, and two Writer calls. The second Validator failed before a ledger prepared event. The Validator base workspace contains .cera_codex_worker_progress.json from operation 0001, and CodexSDKTransport rejects any non-empty workspace in its constructor.`

## Smallest suitable correction

- retained behavior: `persistent Planner thread, fresh Validator and Reader threads, route/model/effort, schemas, prompts, call ledger, archive behavior, no cleanup, and all preserved receipts`
- proposed correction: `allocate a deterministic role-and-operation child directory under each supplied workspace before constructing CodexSDKTransport; require create-new semantics and never reuse or delete an operation directory`
- affected roles: `Planner, Validator, Reader`
- focused test: `transport fake requires an empty workspace and leaves a progress receipt; repeated role calls prove distinct workspaces and unchanged thread lifecycle`
- explicitly excluded changes: `provider calls before freeze, retry/fallback, thread substitution, receipt deletion, output repair, model change, production route, database, installed SillyTavern, deployment, merge, push`

## Provider and attempt boundary

- provider calls before checkpoint: `0`
- remaining creator balance: `598 Codex/Sol, 717 DeepSeek`
- remaining Stage 3 ceiling: `6 Codex/Sol, 4 DeepSeek`
- available distinct-correction reserve: `20 Codex/Sol, 12 DeepSeek`
- fresh V4 maximum: `8 Codex/Sol and 4 DeepSeek; any amount above the remaining Stage 3 ceiling is charged to the distinct-correction reserve`
- Writer attempts: `at most two fresh independent attempts per frozen brief; no merge`

## Verification plan

1. `Exact fake transport fixture reproduces worker residue and proves operation 0002 uses a distinct empty directory.`
2. `Focused sequence-first provider/runtime/HTTP tests.`
3. `Complete repository suite at source freeze.`
4. `Commit provider-free source and result evidence.`
5. `Run fresh Stage 3 V4 under the combined remaining Stage 3 and correction-reserve ceiling.`

## Success criteria

`Repeated Planner, Validator, and Reader operations never reuse a worker workspace; prior progress receipts remain intact; thread persistence/freshness is unchanged; all focused and complete gates pass.`

## Stop and escalation conditions

`Stop before another provider call if gates fail, authority changes, accounting becomes ambiguous, or the fresh V4 repeats operation-workspace reuse.`

## Remaining queue work after this correction

`Fresh Stage 3 V4 two-turn canary -> two isolated SillyTavern runs -> mandatory twenty-accepted-turn campaign.`
