# Sol-medium Fresh-vs-Continued Provisional Session Diagnostic

**Date:** 2026-07-30  
**Status:** terminal diagnostic; no route change or story publication  
**Prompt depth:** Long  
**Reasoner:** `gpt-5.6-sol`, medium  
**Composer:** `deepseek-v4-flash`, thinking enabled  

## Compared policies

1. `fresh_each_turn`: one warm Codex process, but a new ephemeral Codex thread
   for each of the three messages.
2. `continued_thread`: one warm Codex process and one newly-created ephemeral
   thread continued across all three messages.

Both policies received the same explicit testing-only provisional visible
prose. Provisional prose was valid immediate conversational context but was not
committed as canon. The realization verifier was a scripted echo fake, so this
diagnostic does not qualify semantic verification or production publication.

## Terminal results

| Policy | Turn | Result | Wall | Sol input/cache | DeepSeek | Visible words |
|---|---:|---|---:|---:|---:|---:|
| fresh | 1 | pass | 276.296 s | 22,055 / 0 | 127.766 s | 438 |
| fresh | 2 | fail: valid `insufficient_evidence` gate | 29.693 s | 22,657 / 0 | not called | 0 |
| fresh | 3 | pass | 130.038 s | 22,657 / 21,248 | 44.468 s | 275 |
| continued | 1 | fail: incomplete DeepSeek candidate | 111.841 s | 22,054 / 0 | dispatched; receipt lost | 0 |
| continued | 2 | fail: invalid non-ready Reasoner shape | 24.440 s | 35,051 / 21,248 | not called | 0 |
| continued | 3 | fail: invalid non-ready Reasoner shape | 13.029 s | 44,651 / 34,560 | not called | 0 |

The fresh policy passed 2/3 complete provisional turns. The continued policy
passed 0/3. No retry or fallback was used.

The continued turn-1 DeepSeek response reached the provider and returned a
non-`stop` completion, but `DeepSeekChatTransport` raised before constructing a
provider receipt. Therefore the run JSON counter says zero DeepSeek calls even
though one dispatch occurred. This report reconciles that known observability
gap; the raw evidence is left unchanged.

## Behavioral observations

- Fresh turn 1 produced the intended Long domino: Sakura controlled the
  threshold, notified Hana through an explicit knowledge transition, and Hana
  welcomed Ted while preserving his next choice. The 438-word prose was
  materially longer and better developed than the earlier short greeting.
- Fresh turn 2 did receive the displayed prior prose, but Sol returned a valid
  `insufficient_evidence` outcome instead of deciding what “respond
  accordingly” required.
- Fresh turn 3 received the same still-current provisional history and
  independently produced a valid Hana continuation. This proves the behavior
  is stochastic rather than contract-reliable.
- Continued turn 1 had a valid Sol decision, but Flash did not return a complete
  structured candidate. Because no visible prose was accepted, turns 2 and 3
  correctly received no fabricated provisional reply.
- Continued turns 2 and 3 repeated the same invalid status-dependent shape:
  `source_claims:forbidden_for_non_ready_status`. Thread continuation appears
  to have reinforced the prior malformed pattern.

## Cache and latency assessment

Continuation substantially accelerated later Sol calls (20.074 s and 8.716 s)
and increased cache hits, but it also grew total input from 22,054 to 35,051 to
44,651 tokens. Fresh threads still obtained a 21,248-token cache hit when the
stable prefix/request repeated, proving that provider prompt caching does not
require conversational thread reuse.

The two policies had nearly the same total uncached Sol input across three
completed calls (fresh approximately 46.1k; continued approximately 45.9k).
The continued thread therefore improved latency through cache/context reuse,
not by making the request materially smaller.

## Technical conclusion

Do not change the active route to branch-bound continued Codex sessions yet.
The speed benefit is real, but current thread history can reinforce invalid
provider output and grows context every turn. The more urgent shared defects
are:

1. Preserve DeepSeek finish reason, usage, and a privacy-safe provider receipt
   when the HTTP response is returned but the candidate is incomplete.
2. Make direct-reference continuation semantics explicit enough that
   “respond accordingly” plus supplied visible prose has one valid ownership
   path instead of stochastic decision/insufficiency behavior.
3. Prevent a continued thread from copying status-incompatible fields into a
   later non-ready Reasoner draft.
4. Bound and summarize continued-thread history before it grows without limit.

## Evidence

- `full_branch_session_ab_2026-07-30_v1`: pre-dispatch environment failure;
  zero provider calls.
- `full_branch_session_ab_2026-07-30_v2`: complete fresh-thread run plus an
  interrupted continued-thread start. The interrupted call has no terminal
  provider receipt and is not used for comparison.
- `full_branch_session_ab_2026-07-30_v3_continued`: complete continued-thread
  run.

Both completed disposable databases retained zero story artifacts, reported
SQLite integrity `ok`, and had zero foreign-key findings.
