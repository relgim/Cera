# Persistent Codex Transport Qualification Result

**Date:** 2026-07-29  
**Repository:** `D:\AIChatBot\Cera`  
**Status:** four-call live transport qualification passed; provider-free story-route integration passed; D-150 later removed the creator call-ceiling blocker

## Outcome

The supervised persistent no-MCP Codex transport is now qualified for its
narrow role: reuse one app-server process for multiple requests while creating
a fresh ephemeral Codex thread and fresh isolated workspace for every request.
Reasoner and Scene Realization Verifier use separate fixed-role processes. A
request-bound MCP Reasoner call remains an isolated one-shot worker and cannot
use this transport.

The live evidence is:

```text
evaluation/evidence/persistent_codex_transport_probe_2026-07-29_v1/summary.json
SHA-256 f92a08e5cda04c701c74b3d1ddd98fe1abc0da733959f56545c0eb43cd07f5f3
```

It records four unique one-shot request identities and safe provider receipts:

| Role | Sequence | Provider duration | Process result |
|---|---:|---:|---|
| Scene Reasoner | 1 | 2,973 ms | one process, first request |
| Scene Reasoner | 2 | 2,633 ms | same process, fresh thread/workspace |
| Scene Realization Verifier | 1 | 4,062 ms | separate process, first request |
| Scene Realization Verifier | 2 | 2,840 ms | same verifier process, fresh thread/workspace |

Every call returned the exact closed non-story schema, used no tool, retained
no prompt/output/story/private evidence, made no story-authority write, and had
zero retry/fallback. Both processes closed after their second request; no
persistent worker remained.

## Provider-free integration

The active continuous runner is prepared as v12 but has not been dispatched.
It now:

- refuses before evidence creation or provider setup when the full 20/10 route
  budget is unavailable;
- activates persistent reuse only when the exact passed summary bytes match the
  pinned SHA-256 and every four-call safety/process/role/receipt invariant;
- uses the persistent Reasoner only on no-MCP turns;
- uses an isolated one-shot Reasoner on the request-bound MCP memory turn;
- uses a separate persistent Verifier for all turns;
- records role-specific process-launch and request-submission counters in
  terminal evidence;
- closes both process trees on success or failure without resubmission;
- counts persistent probe dispatch markers in the repository-wide live ledger.

The future v12 story route is materially different from terminal v11. It
begins at the original V1.2 doorway but follows Tomi's training-return strain,
choice and relationship boundary; performs a paraphrased, owner-private
`T-M07` lookup; exercises Tomi/Yuuni/Aoi participant and floor selection; then
performs restart, fork, regeneration, and continuity checks. A real V1.2
provider-free query-plan test proves the v12 paraphrase resolves `T-M07`
without relying on its exact label.

Adversarial tests reject changed evidence, rehashed semantic mutations,
duplicate provider identities, wrong model/role, unsafe retention, extra
process launches, MCP use through the persistent runner, and cross-role reuse.

## Verification

```text
Ran 376 tests in 185.327s
OK
```

The complete provider-free suite includes real Genesis retrieval, privacy and
knowledge filtering, branch/regeneration isolation, restart/atomicity,
provider-schema/domain differential checks, malformed output, selected cast,
protected-user realization, publication, and the new transport proof/integration
tests.

## Conservative live-call ledger

The probe consumed exactly four Sol calls and no DeepSeek call:

```text
Sol-medium:       40 / 50 used; 10 remain
DeepSeek V4 Pro:  18 / 25 used;  7 remain
Fresh 10 turns:   20 Sol + 10 DeepSeek required
May begin:        no
```

That block records the then-controlling D-146 ceiling. D-150 subsequently
raised the total ceilings to 500/500. The same conservative ledger therefore
now leaves 460 Sol and 482 DeepSeek calls and permits the fresh 20/10 route.

The persistent transport is qualified; the story route is not. D-150's
500/500 total ceiling authorizes bounded provider use toward the active goal
but does not authorize
retry, fallback, production binding, route promotion, deployment, an external
handler, or SillyTavern activation before the ten-turn gate passes.

## Advisory review

ChatGPT Pro returned the exact advisory verdict
`CERA_PERSISTENT_TRANSPORT_AND_V12_ACCEPTED`. It agreed that mixed transport
ownership is capability-driven: Python selects the transport before dispatch,
no-MCP Reasoner and Verifier requests may use their separate qualified
persistent processes, and request-bound MCP Reasoner work remains isolated
one-shot. A failed request is never transferred or retried through the other
transport.

Pro also accepted the exact-byte plus semantic activation proof and found v12
materially different from v11, provided `T-M07` remains only fixture content
and transport selection depends on the generic evidence-tool obligation.
Codex independently checked those conditions against the implementation and
tests and accepts the narrow verdict. No additional provider-free correction
is required before the creator call-ceiling decision. This review does not
qualify the ten-turn story route or authorize any live call or product action.
