# CERA Continuous SillyTavern Overnight V2 - Progression 2 Result

queue_revision: `0025`
task_id: `continuous-sillytavern-role-conflict-and-transport-live-regression-v2`
status: `completed`
checkpoint_id: `2026-08-02-continuous-sillytavern-overnight-v2-001`
base_git_sha: `2b29675352cf971be95a32a19683513f51b69cbc`
implementation_git_sha: `302bf33550007742ab988ba97581dd157bc284e9`
implementation_tree_sha: `87199150ec4d1318848c102eaf7618191c86ef17`
external_provider_calls: `0`
retry_count: `0`
fallback_count: `0`

## Result

A privacy-safe deterministic fixture now reproduces the exact four
mutually-exclusive role conflicts observed after the one consumed Sol-medium
call in immutable V1 Run 001. It binds only safe hashes and structural
metadata: the provider receipt, operation telemetry, stored-thread identity,
tool sequence, output-schema path, strict validator path, terminal failure,
and six-state call lifecycle. It retains no raw provider output, private
provider state, secrets, or real character identifiers.

The invalid provider-shaped Planner result reaches the real strict
`RichPlannerSequenceV1` / `CharacterRoleLedgerV1` validation path and fails
after exactly one simulated transport dispatch. It is not normalized,
coalesced, deleted, reassigned, retried, sent to the Composer, or made
Accept-eligible. The corrected Turn 1 fixture preserves every assertion using
five causally ordered split beats, with each character in exactly one role
array per beat, and passes the same strict path.

Planner, Validator, fork-child, auxiliary, failed, and final physical thread
tests keep transport live until terminal-evidence V5 closure. They prove
verified archival, failed resume, failed backend selection, failed coordinator
selection, and closed lineage on success and failure. Cleanup failures remain
additive to the first semantic/provider-stage failure and cannot overwrite its
type, message, stage, or consumed-call count. HTTP-server cleanup failures also
cannot prevent later thread terminalization or provider-context closure.

Parent/child recovery now freezes a self-hashed, artifact-bound
`PARENT_RUN_RECONCILIATION.json` even when a child never creates
`RUN_RESULT.json`. The published campaign result binds every local child
reconciliation by file and receipt hash. Tampering, missing local
reconciliation evidence, extra index entries, malformed call order, and
artifact substitution fail closed.

Consumed-call reconstruction follows the durable conservative ledger rule:
transport-marked calls count once, unresolved prepared/worker calls count as
consumed, and proven local pre-transport failures do not. Attempted child
stages remain distinct from consumed calls. Incomplete child pass claims
freeze as terminal failures. Same-execution restart recovery preserves one
validated pass and its restart boundary; changed execution bytes reset the
streak. Every recovery uses only the next unused immutable run identity and
tracks cumulative Codex-family and DeepSeek counts under the 40-call campaign,
800 Codex-family, and 800 DeepSeek ceilings.

## Provider-free verification

- Exact role-conflict, corrected-sequence, lifecycle, reconciliation,
  tamper, partial-call, and campaign regression gate: `27/27 passed` in
  `2.650` seconds.
- All additional affected lineage, Job 4 harness, Planner/Validator,
  correction, documentation, SillyTavern integration, and world regressions:
  `137/137 passed` in `45.097` seconds.
- Active-profile and repository source-inventory gate: `4/4 passed` in
  `0.392` seconds.
- Compilation of the three Python files, direct repository-venv imports, and
  campaign `--help`: passed.
- `git diff --check`, exact four-file staged boundary, `git show --check`, and
  local commit checks: passed.
- One preliminary CLI assertion used PowerShell's linewise `-notmatch` on an
  array and falsely reported the present flag as missing. The corrected joined
  output check passed. This operator-check error made no provider, repository,
  story, database, route, or service change.
- Persistent human-test SQLite remained SHA-256
  `bfbf23eb2fa7199fad38e8fb3f1ae0d6b547467f17ed68e84b7115275a2fc555`,
  integrity `ok`, foreign-key findings `0`.
- Active profile remained `cera.active_runtime.d180.v1`, SHA-256
  `f74347604d9adb7be0ded2a3c9c62e7076aca4d5d773aa8a3c97f8e7bcee0801`.
- CERA qualification port `5113` had no listener. The pre-existing Node/
  SillyTavern listener on port `8000` was observed read-only and not altered.
- No campaign process remained after tests.
- No story, persistent database, active route, installed SillyTavern,
  deployment, merge, remote, push, retry, fallback, hidden repair, or external
  provider effect occurred.

## Historical preservation and accounting

- V1 Run 001 result SHA-256:
  `f1617de44f31c9e0408eb3d75c422dc7a9ef33447cca2fe63fa26915e0950c22`.
- V1 Run 001 provider ledger SHA-256:
  `dfd2572e208104614fa9e5f2ed0e42246a7d4d445b201e906bbcac6842fc54fd`.
- V1 Run 001 execution manifest SHA-256:
  `3dfad6b541df37c1044348a5a05e308cb2e43cd5c9faf97bb4987c6cffc5c54d`.
- Historical campaign result SHA-256 remained
  `65d4e92c0872f6ffd49ac4d218af9f4a139938e1bb8f69a003e7198cca8f41d1`.
- Read-only recovery reports one preserved run, one Codex-family call, zero
  DeepSeek calls, and Run 002 as the next unused identity.
- Cycle 21 accepted response SHA-256 remained
  `c20c0ad2726908e8015cd2b88f0fcb645d272e6ac4c755e1e636b5835aaa4e11`.
- Cycle 21 consumption receipt SHA-256 remained
  `4973cca7344eeea1b6ebbd3fe31883399f856b7c608b4ac1ea7f10ec3a42939e`.

## Source identities

- `scripts/run_sillytavern_continuous_v3_campaign.py`:
  `795290d91613f10a60b4b1e915bbe2fc4bee669c3515f43a6795b06a59ab6060`
- `src/cera/sillytavern/campaign.py`:
  `5b819149da2c7935237f3c919562e6b752d9b495f4259128d45202870d6abc2d`
- `tests/fixtures/continuous_v1_run001_role_conflict_v1.json`:
  `c090180ff88fc7fc314ec227fb275d1a1f38a07992e8a9c4c34f2d259d857c16`
- `tests/test_continuous_role_conflict_regression.py`:
  `8e4aa8916221b9b6a19ac207d3796c22eb8f1280c9b8c6f858228b4cefa6c143`

## Next authorized operation

Freeze and notify this progression, reread `CURRENT.md`, then continue only
under the resulting active pointer. Queue 0025 currently names provider-free
Progression 3:
`continuous-sillytavern-general-manual-route-and-two-run-readiness-v2`.
