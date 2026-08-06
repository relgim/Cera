# CERA Codex Self-Continuation Plan

plan_schema_version: `cera.codex_self_continuation_plan.v1`  
plan_id: `self-continuation:q0062:0037:sequence-first-responder-kind-derivation-v1`  
queue_revision: `0062`  
created_at_utc: `2026-08-06T06:28:13.1127391Z`  
status: `plan_written_proceeding`  
execution_repository: `D:\CP25\source`  
execution_git_sha: `a83c0088be53d43957bce5a6c9ac79a3f364cd0b`  
execution_tree_sha: `44ef968bcd2a126aa52019bd5e1879be0c5b6fd2`

## Trigger

- current stage/task: `Queue 0062 Stage 3 fresh V2 preparation after the completed local-key schema correction`
- failed identity supplying exact evidence: `2026-08-06-cera-sequence-first-stage3-live-v1`
- evidence: `The exact preserved Planner output assigned character:mia as owner of a material_continuity item whose meaning was that Mia remained backgrounded.`
- affected contract: `SequenceDraftV1.responding_character_ids currently includes every non-Ted owner without considering item kind.`
- manager authority check: `CURRENT.md remains Queue 0062 and all mandatory authority was reread before diagnosis.`

## Classification

- issue class: `A+B`
- exact blocker: `A structural continuity assertion can make a present-but-backgrounded NPC a derived responder, exposing responder-scoped voice cues and removing that NPC from the mechanically derived backgrounded set even though no action, dialogue, perception, private-state, or remote-communication response was planned.`
- genuine creator decision required: `false`
- reason existing authority resolves desired behavior: `Queue 0062 and the Stage 6 integration authority require Planner-selected responders, responder-scoped voice cues, mechanically derived backgrounding, and a live test containing a present-but-backgrounded NPC.`

## Current product objective

`Keep structural semantic ownership available for evidence and state meaning while deriving responders only from item kinds that actually express a character response.`

## Evidence and root-cause hypothesis

`The live V1 output is a falsifying fixture: material_continuity owned by Mia currently enters responding_character_ids. The root is an over-broad derived view, not provider behavior and not presence authority.`

## Smallest suitable correction

- retained behavior: `all item kinds, structural owner semantics, evidence ownership, presence validation, protected-user boundaries, Writer wire, Validator/Reader contracts, persistence, HTTP, and acceptance behavior`
- proposed correction: `define the responder-producing item-kind set from the existing owner-required behavioral kinds; filter responding_character_ids through that set; add exact focused tests proving structural ownership does not foreground Mia while behavioral ownership still does`
- expected files/contracts: `src/cera/sequence_first/contracts.py and focused tests only`
- explicitly excluded changes: `provider calls, provider schema, model prompts, owner removal, output repair, retries, fallbacks, production route, database, installed SillyTavern, deployment, merge, push`

## Provider and attempt boundary

- provider calls before checkpoint: `0`
- remaining creator balance: `601 Codex/Sol, 719 DeepSeek`
- remaining original Stage 3 ceiling: `9 Codex/Sol, 6 DeepSeek`
- fresh V2 maximum: `at most 8 Codex/Sol and 4 DeepSeek by using at most two independent Writer attempts per turn`

## Verification plan

1. `Focused contract test with material_continuity owned by Mia proves Mia remains backgrounded.`
2. `Existing behavioral responder, presence, voice-cue, provider, and route tests remain green.`
3. `Complete repository suite at the corrected source freeze.`
4. `Commit the provider-free correction and result evidence.`
5. `Run fresh Stage 3 V2 only after all gates pass.`

## Success criteria

`Structural ownership never creates a responder; behavioral item ownership still does; backgrounding and voice-cue selection follow that exact derived view; focused and complete suites pass with zero provider calls.`

## Stop and escalation conditions

`Stop before another provider call if focused/full gates fail, authority changes, accounting becomes ambiguous, or the fresh V2 exposes the same responder-kind defect.`

## Remaining queue work after this correction

`Fresh Stage 3 V2 two-turn canary -> two isolated SillyTavern runs -> mandatory twenty-accepted-turn campaign.`
