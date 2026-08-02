# Continuous V3 ordinary manual-test route

## Status and boundary

This is an isolated, loopback-only, non-production route for ordinary typed
turns through the accepted Continuous V3 Planner, Composer, Validator, exact
creator review, strict Accept, Scene Change, persistence, and accepted-context
synchronization path.

The route is intentionally provider-free until a newer Pro-owned queue grants
live dispatch authority. Its local scripted transports exercise the complete
ten-stage shape while making zero external provider calls. It must never be
described as a live model-quality result.

The route identity is:

- endpoint: `http://127.0.0.1:5114/v1`;
- virtual model: `cera-continuous-v3-manual`;
- profile: `cera.continuous_v3.manual.v1`;
- service: `cera-sillytavern-continuous-v3-manual`;
- default session: `cera-continuous-manual`;
- database policy: isolated resettable manual root;
- automatic acceptance, retry, fallback, and provider substitution: disabled.

The repository profile is
[`continuous_v3_manual_profile.json`](../../integrations/sillytavern/continuous_v3_manual_profile.json).
It is not installed over the existing `cera-alpha` preset.

## Clean reset

Run from `D:\AIChatBot\Cera`:

```powershell
$ManualRoot = 'D:\AIChatBot\Cera\runtime\manual\continuous_v3'
.\.venv\Scripts\python.exe scripts\run_cera_sillytavern_continuous_manual.py reset --root $ManualRoot --session-id cera-continuous-manual --confirm-reset
```

Reset refuses an unowned root, an active process record, an occupied port 5114,
or an overlong Windows root. Keep the root path short: deeper transaction paths
are bounded against the Windows path limit before state is created.

Reset creates fresh and separate authority SQLite, Continuous world, branch,
session, ingress authority, provider workspace, evidence, process, and review
state. It inventories and hashes the active D-180 profile, persistent human-test
database, installed SillyTavern settings/card/bridge/extension files, and
background loopback port state.

At generation zero, Sakura is the direct NPC because the authoritative Genesis
starting scene assigns her the formal doorway contact. After the first accepted
turn, continuation uses the durable accepted scene cast rather than that
generation-zero default.

## Start and verify

```powershell
.\.venv\Scripts\python.exe scripts\run_cera_sillytavern_continuous_manual.py start --root $ManualRoot
.\.venv\Scripts\python.exe scripts\run_cera_sillytavern_continuous_manual.py status --root $ManualRoot
Invoke-RestMethod http://127.0.0.1:5114/health
Invoke-RestMethod http://127.0.0.1:5114/v1/models
```

Before sending a turn, verify all of the following:

- status is `running`;
- `pid_alive`, `port_open`, `identity_ok`, and `execution_identity_ok` are true;
- health reports the exact manual model and profile;
- `/v1/models` contains only `cera-continuous-v3-manual`;
- `external_provider_calls_authorized` remains zero in the root manifest.

The launcher recomputes all execution-critical source, profile, Genesis, Git
source revision and scoped worktree status, model, port, and route bytes. Any
source or route drift requires a clean reset; it cannot continue under the old
identity. A later evidence-only commit does not rewrite the source revision,
while a commit touching execution source does.

The repository profile is also checked as one exact semantic object. Changing
its nested Custom Endpoint body, Scene Change body, review workflow, or
installed-relay boundary fails before service startup even if the JSON remains
syntactically valid.

## Connect a separate SillyTavern Custom Endpoint preset

Do not overwrite the installed `cera-alpha` preset. Create a separate Custom
Endpoint preset with:

- URL: `http://127.0.0.1:5114/v1`;
- model: `cera-continuous-v3-manual`;
- streaming: off;
- automatic retry/fallback: off.

In **Customize Additional Parameters → Include Body Parameters**, use:

```yaml
cera_session_id: cera-continuous-manual
cera_profile_id: cera.continuous_v3.manual.v1
cera_scene_change: false
```

SillyTavern's existing Custom Endpoint machinery merges this YAML into the
outbound request, so no installed SillyTavern source or user-story file needs
to change. A different model, profile, or session is rejected before any local
stage runs.

The installed same-origin creator-review relay remains deliberately bound to
the active D-180 service on port 5101. Progression 3 does not mutate or restart
that installed relay. Therefore manual-port reviews are inspected and decided
with the repository CLI below. The story candidate shown by SillyTavern remains
provisional until that exact CLI decision succeeds.

## Submit, inspect, Accept, or decline

An ordinary turn can be submitted from the separate SillyTavern preset or
directly through the CLI:

```powershell
.\.venv\Scripts\python.exe scripts\run_cera_sillytavern_continuous_manual.py submit --root $ManualRoot --message 'Ted knocks and asks Sakura whether this is the Hanezawa residence.'
```

Discover the one unresolved review without copying an ID from SillyTavern:

```powershell
.\.venv\Scripts\python.exe scripts\run_cera_sillytavern_continuous_manual.py pending-review --root $ManualRoot
```

Inspect an explicit review:

```powershell
$ReviewId = 'review_packet:replace-with-the-returned-id'
.\.venv\Scripts\python.exe scripts\run_cera_sillytavern_continuous_manual.py review --root $ManualRoot --review-id $ReviewId
```

Strict Accept:

```powershell
.\.venv\Scripts\python.exe scripts\run_cera_sillytavern_continuous_manual.py decide --root $ManualRoot --review-id $ReviewId --action accept
```

Exact decline:

```powershell
.\.venv\Scripts\python.exe scripts\run_cera_sillytavern_continuous_manual.py decide --root $ManualRoot --review-id $ReviewId --action decline
```

The route blocks the next turn while a review is unresolved. Accept consumes
the exact candidate, SequencePlan, Validator package, assessment receipt,
source, ingress, active-cast, execution, and process binding. It makes no
provider call. The accepted active cast is durable scene state, so a character
does not disappear merely because she was silent in the latest accepted prose.
Decline writes a bound discard receipt and does not change accepted state.

Before either decision crosses into the shared Continuous transaction, the
manual state writes a self-hashed decision intent. While that intent is
pending, both Accept and decline are disabled and a duplicate decision is
rejected. If the shared transaction completed but the manual review could not
finish, restart reconciles only an exact fully synchronized Accept receipt or
exact durable Decline receipt. It never repeats the decision. If the outcome
cannot be proven, startup fails closed; preserve the root for diagnosis and use
a clean reset only after the managed process and port are proven absent.

## Scene Change

For a direct CLI turn:

```powershell
.\.venv\Scripts\python.exe scripts\run_cera_sillytavern_continuous_manual.py submit --root $ManualRoot --scene-change --message 'The next morning, Ted is in the kitchen with Mia and asks about Sakura.'
```

For one SillyTavern turn, change the preset's Include Body Parameters to:

```yaml
cera_session_id: cera-continuous-manual
cera_profile_id: cera.continuous_v3.manual.v1
cera_scene_change: true
```

Immediately restore `cera_scene_change: false` after the request. A Scene Change
is rejected unless the current scene has accepted turns and the new request
explicitly names at least one new-scene character (or explicitly selects the
whole cast). It does not silently carry the old scene's cast into a new scene.
It adds exactly the Scene Summary Validator stage before the next Planner
stage.

## Stop, restart, unresolved review, and recovery

```powershell
.\.venv\Scripts\python.exe scripts\run_cera_sillytavern_continuous_manual.py stop --root $ManualRoot
.\.venv\Scripts\python.exe scripts\run_cera_sillytavern_continuous_manual.py start --root $ManualRoot
```

Stop uses an identity-bound local control request rather than Windows
`SIGTERM`. Before shutdown, the transient Validator thread is archived and
proved non-selectable while the Planner's accepted restart state is retained.
The parent validates the terminal record, consumed stop request, and thread
evidence before reporting success.

If stop terminalization fails, the child freezes a signed failure record and a
hash-bound copy of the rejected stop input, closes the loopback service, and
marks the terminal record failed. The parent reports failure only after the
process and port are closed; it never mistakes that path for a successful stop
or leaves the failed watcher running as an orphan.

If a process dies before terminalization and both its PID and port are proven
absent:

```powershell
.\.venv\Scripts\python.exe scripts\run_cera_sillytavern_continuous_manual.py recover-process --root $ManualRoot
```

Recovery never reuses the dead process identity. After a restart, an unresolved
candidate can still be inspected and exactly declined, but Accept is disabled
because the prior process's uncommitted in-memory candidate cannot be
reconstructed safely. Start a new turn only after declining it.

## Isolation and clean handoff

Stop the route, then verify:

```powershell
.\.venv\Scripts\python.exe scripts\run_cera_sillytavern_continuous_manual.py verify-isolation --root $ManualRoot
```

`passed: true` proves:

- the persistent human-test database is byte-identical;
- active D-180 identity is unchanged;
- installed SillyTavern settings, card, request bridge, review plugin, and
  extension bytes are unchanged;
- background ports 8000 and 5113 have not changed state;
- the active D-180 review/service port 5101 has not changed state;
- port 5114 is closed and no managed manual process remains.

For a generation-zero handoff, run the clean-reset command again. No provider
process is created by this provider-free route.

## Provider-free two-run readiness proof

After source and documentation bytes are frozen, use a fresh attempt identity
and short manual parent:

```powershell
$Attempt = '2026-08-02-cera-p3-readiness-attempt-001'
$Evidence = 'D:\AIChatBot\Cera\.chatgpt\pro-review\checkpoints\2026-08-02-continuous-sillytavern-overnight-v2-001\PROGRESSION_3_READINESS'
$RunRoots = 'D:\AIChatBot\Cera\runtime\manual\p3r1'
.\.venv\Scripts\python.exe scripts\run_cera_sillytavern_continuous_manual_readiness.py --attempt-id $Attempt --evidence-root $Evidence --manual-parent $RunRoots
```

The proof runs two fresh databases/worlds/branches/sessions through three turns,
two scenes, exact review, and strict Accept. Each run uses ten local scripted
transport invocations and zero external provider calls, stops under a distinct
process identity, freezes a short-name ZIP plus complete internal file hashes,
and resets its manual root to generation zero.

The readiness manifest binds Progressions 1 and 2, immutable Cycle 21 Run 001,
its one Sol debit, remaining global ceilings of 799 Codex-family and 800
DeepSeek calls, and fresh never-started V2 live identities. It does not activate
those identities or grant provider-call authority.

On any failure, preserve the attempt evidence and use a new attempt ID after a
provider-free correction. Never retry a failed stage or attempt identity in
place.
