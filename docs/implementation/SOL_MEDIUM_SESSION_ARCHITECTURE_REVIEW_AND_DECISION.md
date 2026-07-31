# Sol-Medium Session, Context, and Caching Review

**Date:** 2026-07-30  
**Decision:** D-171 review accepted; D-172 provider-free implementation authorized  
**Authority:** advisory sources plus independent Codex owner review; creator decision remains final

## Advisory ChatGPT material

The creator supplied two ChatGPT-authored Markdown files. They remain advisory
evidence and are not CERA runtime dependencies.

| Source | Original path | Verified SHA-256 |
|---|---|---|
| Optimization findings | `C:\Users\Ted\Downloads\CERA_SOL_MEDIUM_OPTIMIZATION_FINDINGS_2026-07-30.md` | `d13b417c141a2a90363aaf53f6e61866bfb0770457864ee036d2b69e3d2777b4` |
| Proposed optimization command | `C:\Users\Ted\Downloads\CODEX_COMMAND_CERA_SOL_OPTIMIZATION_HIGH_UPSIDE_LOW_DOWNSIDE_V1.md` | `986b5e00f728eb4c59d1ccbba3e83ef2fc256813be1e506b7b502971045d8c76` |

ChatGPT recommended Sol-medium as the baseline; separated prompt caching from
persisted reasoning; proposed measuring cache reads/writes, verbosity,
`reasoning.context`, prompt duplication, deferred MCP definitions, and
resumability; preserved Python authority; and excluded Fast, Pro, adaptive
routing, retry/fallback, Detailer, and hidden reasoning as story memory.

## Codex independent findings

The current CERA `PersistentNoMcpCodexRunner` reuses one app-server process but
creates a fresh ephemeral thread for every request. This is process reuse, not
branch or conversational persistence. The active Reasoner prompt also resends
its stable instruction block and complete packet every call.

Measured continued Sol evidence was:

| Turn | Total input | Cached input | Uncached input | Wall | Result |
|---|---:|---:|---:|---:|---|
| cold | 22,055 | 0 | 22,055 | 95.326 s | valid |
| warm 2 | 34,610 | 21,248 | 13,362 | 29.285 s | typed failure |
| warm 3 | 44,308 | 33,536 | 10,772 | 17.384 s | typed failure |

Therefore `22k -> 13k -> 10k` described uncached input. Logical context grew.
Cached tokens still occupy context; caching reduces repeated prefix processing
and may reduce latency/usage accounting. Output/reasoning also fell sharply,
so the latency change cannot be attributed only to caching. The two warm Sol
turns do not establish production narrative quality because both failed typed
acceptance.

The current doorway-shaped packet measured about 21,754 bytes, of which about
18,482 bytes were seed dossier. The stable prompt instruction prefix was about
16,355 bytes and the provider schema about 18,546 bytes. This is enough stable
material to make branch persistence and prefix caching worth qualifying, but
it does not justify removing evidence.

Official OpenAI documentation supports exact-prefix caching, separate
conversation/reasoning state, verbosity controls, and app-server thread
start/resume/fork/compact primitives. The pinned high-level Python Codex SDK
does not currently expose every cited Responses API control. CERA cannot yet
claim explicit cache keys, cache-write accounting, or an effective
`reasoning.context` selection. Unsupported metrics must remain null/unknown.

## Codex opinion and decision

ChatGPT's main direction was accepted, with these corrections:

- use an accepted-checkpoint tree, not one mutable continued thread;
- fork every provisional candidate from accepted ancestry;
- promote only after creator acceptance and Python's atomic commit;
- keep rejected children isolated and carry forward only explicit feedback;
- make feedback branch-local by default and global only by explicit creator action;
- never infer a rule from a bare decline;
- regenerate from the replaced artifact's parent checkpoint;
- reconstruct complete authority on incompatibility, provider loss, or rotation;
- keep Reasoner and verifier sessions independent;
- split stable instructions from the variable packet byte-for-byte before any
  prompt deletion experiment;
- reference previously materialized evidence only on exact version/hash/
  section match from accepted ancestry;
- keep hard Python validation regardless of creator review.

Raw rejected prose retention was technically optional. V1 deliberately keeps
the existing privacy-minimal behavior: purge prose after resolution and retain
candidate hash, isolated checkpoint, reasons, diagnostic feedback, and typed
constraints. This is sufficient to prevent a known error without treating the
rejected story as history.

The creator approved branch-local constraints by default. Restart-persistent,
non-ephemeral provider rollout storage is deferred until CERA has a working,
bug-free branch-session model and a later privacy/retention decision.

## Adopted, deferred, and rejected recommendations

### Adopted now

- Sol-medium remains the optimization baseline.
- Provider-neutral session port and Python-owned lifecycle ledger.
- Accepted checkpoint plus candidate forks.
- Stable/variable prompt separation with exact-equivalence validation.
- Typed context authority deltas, creator constraints, and safe usage receipts.
- No retry, fallback, route substitution, Detailer, or hidden reasoning memory.

### Deferred to later live qualification

- live fresh-versus-persistent Sol benchmark;
- `text.verbosity=low` comparison;
- explicit cache-key/breakpoint behavior;
- `current_turn` versus `all_turns` reasoning context;
- non-ephemeral provider rollout survival across computer restart;
- deferred MCP loading and any transport extension needed to observe missing metrics.

These variables must be isolated rather than changed together.

### Not accepted as established fact

- that cached input shrinks logical context;
- that the warm failed Sol turns prove story quality;
- that API dollar pricing directly describes ChatGPT Pro weekly quota usage;
- that process reuse supplies conversational state;
- that creator acceptance can override privacy, branch, consent/capacity,
  protected-user, evidence, schema, or atomic-publication validation.

## Later benchmark boundary

After provider-free closure, the minimum useful live comparison is the same
six-turn sequence on fresh sessions and on accepted-checkpoint candidate forks:
cold turn, accepted continuation, adjustment, explicit rejection, regeneration,
and sibling branch. Record complete token/cache/byte/timing/session/validity/
quality/privacy evidence. Use one attempt, no retry/fallback, and no story-state
write. DeepSeek is excluded from the isolated Reasoner comparison.

No live benchmark is authorized by this review or D-172.
