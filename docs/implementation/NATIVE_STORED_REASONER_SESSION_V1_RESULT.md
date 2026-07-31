# Native Stored Reasoner Session V1 Result

**Date:** 2026-07-31  
**Decision:** D-174  
**Status:** provider-free implementation complete; live canary not authorized  
**Active route:** unchanged Sol-medium fresh-thread development route

## Outcome

D-173 failed because CERA attempted to fork an ephemeral Codex thread. D-174
keeps D-172's accepted-checkpoint architecture and replaces that unsupported
provider assumption with Codex app-server stored rollouts.

The implemented provider lifecycle is:

```text
stored accepted checkpoint
-> stored candidate leaf fork
-> one Reasoner turn on that candidate
-> creator acceptance + Python commit promotes the candidate checkpoint
or
-> rejection/failure deletes only the candidate leaf
```

Regeneration selects the stored thread mapped to the replaced artifact's
accepted parent checkpoint. A CERA branch fork gets a different provider thread
and CERA session identity. Restart resumes the stored thread mapped to the
accepted checkpoint. A missing or compatibility-incompatible thread requires a
Python reconstruction; it never triggers an automatic provider call.

This follows the native lifecycle documented by
[Codex App Server](https://learn.chatgpt.com/docs/app-server.md): stored
`thread/start`, `thread/resume`, `thread/fork`, `thread/archive`, and
`thread/delete`. The installed Python SDK exposes all required high-level
methods except delete. Its generated protocol contains the typed delete RPC;
CERA isolates that compatibility call in one provider adapter rather than
leaking a private SDK dependency into core code.

## Safety and authority

- Python's branch, generation, accepted head, evidence snapshot, and creator
  review remain final authority.
- A provider conversation is advisory cache/context, never canon, memory, or
  character knowledge.
- Accepted checkpoint threads are immutable: CERA forks before every new
  Reasoner candidate and does not append another Reasoner turn to the parent.
- Rejected/failed threads are Python-proven leaves. They are deleted before the
  corresponding terminal SQLite transition, preventing raw rejected context
  from entering later accepted ancestry.
- Accepted or branched ancestors are archived, never recursively deleted.
- Acceptance adds no provider call and does not depend on undocumented raw-item
  injection. The next canonical packet reasserts accepted Python state.
- No retry, fallback, Composer Detailer, story write, or route promotion was
  added.

## Request-bound evidence

The new stored-turn worker starts one supervised app-server process for the
call, resumes the exact candidate thread, and recreates only the current MCP
loopback binding and bearer in that process. A previous turn's bearer is not
reused. The accepted provider thread may contain earlier advisory context, but
every current evidence claim is still constrained by the new Python packet,
snapshot, MCP allow-list, and post-call validation.

This worker remains inactive until one minimal live canary proves that the
installed app-server accepts stored resume with the request-local MCP override.

## Persistence and observability

SQLite migration 17 adds append-only
`reasoner_provider_thread_custody_events`. Each event stores only:

- CERA session/checkpoint IDs;
- hashed provider thread and parent identities;
- stored-local mode and checkpoint role;
- allocation/acceptance/deletion/archive/resume/missing state;
- whether the raw local provider context still exists;
- the fixed fact that provider context is not story authority.

Raw provider prompts, output, reasoning, evidence, bearer tokens, and story
prose are absent from custody events.

## Provider-free verification

Focused tests cover:

- stored root allocation, candidate fork, acceptance, rejection, and restart;
- exact parent/checkpoint mapping and branch/regeneration behavior;
- rejected/failed leaf deletion with accepted-parent retention;
- prevention of deletion when a live descendant exists;
- delete failure leaving the candidate open and accepted pointer unchanged;
- non-ephemeral SDK arguments and exact generated `thread/delete` projection;
- per-turn MCP environment reconstruction with no bearer leakage;
- migration 17 integrity and append-only custody decoding;
- existing compatibility, privacy, evidence-ancestry, and creator-constraint
  invariants.

Terminal verification:

- focused stored-session/transport/storage regression: 68/68 passed;
- complete provider-free repository suite: 556/556 passed in 313.292 seconds;
- `python -m compileall -q src tests`: clean;
- documentation validation: clean with zero findings.

The complete suite's only HTTP traffic was its existing authenticated local
loopback MCP protocol test. No live provider call occurred.

## Deliberately not changed

- No provider was called.
- The D-173 evidence directories remain unchanged.
- The active SillyTavern executor still uses the qualified fresh-thread route.
- No live benchmark, five-case rerun, story-state write, production binding,
  Adult publication, external handler, promotion, or deployment occurred.

## Next gate

Authorize one minimal non-story Sol-medium stored-session canary:

1. create one stored root without a model call;
2. fork one stored candidate;
3. run one schema-valid Reasoner probe with a request-bound MCP bridge;
4. stop after recording resume/fork/MCP/custody evidence;
5. delete the disposable candidate/root if safe.

If any lifecycle or MCP rebinding step fails, stop without story cases. A later
five-case run and active SillyTavern route switch require separate authority.
