# CERA Creator Decisions, Tradeoffs, and Engineering Lessons

status: living creator-and-engineering record
owner: Ted
maintainer: Codex
created: 2026-08-09
scope: cross-queue product direction, architecture rationale, known mistakes, and current open decisions

## 1. Purpose and authority

This is the durable, human-readable record of what CERA is intended to become,
why important choices were made, what has gone wrong during development, and
which questions remain open.

It is deliberately stored in the manager repository's mutable handoff area and
linked from the organized `D:\Cera` entry point. It is not inside a historical
queue or campaign directory. Historical queue documents, manifests, receipts,
and frozen evidence remain immutable.

This document records creator direction and engineering conclusions. It does
not by itself activate a provider call, production route, deployment, database
mutation, installed SillyTavern change, merge, push, or historical rewrite.
Implementation work must adopt each decision through the current governed
repository authority.

## 2. Product goal

CERA is a long-running, branch-aware character and story system operated from
SillyTavern. The user supplies the initial domino. CERA determines the causal
and psychological continuation, and the Writer realizes it as readable prose.

The priority is a dependable roleplay product that:

- respects character logic and development;
- preserves branch-local memory and private knowledge;
- lets the user's prompt control direction without automatically controlling
  independently governed characters;
- supports ordinary and separately routed adult scenes;
- survives Accept, Decline, Regenerate, Replan, restart, and new-chat flows;
- remains understandable and adjustable instead of accumulating one-off prompt
  rules;
- can be used for several consecutive story turns without API, review-ID,
  persistence, or routing failures.

The qualification campaign is primarily a technical reliability exercise. It
is not an attempt to make a probabilistic Writer perfect or deterministic.

## 3. World, Genesis, and branch model

### 3.1 New chat means a new world branch

Starting a new SillyTavern chat automatically creates a new world/branch
directory. It uses the newest accepted Genesis revision available at creation
time. The directory receives a branch-local copy or materialization of that
Genesis package and its relevant character information.

Branching an existing chat is different from starting a new chat. A branch fork
copies the source chat's complete world directory at the selected fork point,
including its pinned Genesis revision, accepted history, provisional lineage,
current derived state, indexes, and applicable settings. The child receives a
new branch identity and may diverge without mutating the source chat.

Runtime Codex may read only:

- its assigned world directory;
- the Genesis and character material installed in that directory;
- accepted and provisional branch records stored for that world;
- indexes and generated views derived from those same records.

It must not read another chat's logs, another world's private state, unrelated
repositories, or arbitrary drives. Even accidental cross-chat access is a
branch-corruption defect.

### 3.2 Genesis is a starting state, not a permanently frozen current state

Genesis establishes the facts and character state at the branch's starting
point. Historical Genesis bytes remain immutable evidence, but the story's
current state evolves through later accepted events.

Example:

```text
Genesis records a character's age at the story start.
An accepted birthday event occurs later.
The original Genesis record remains unchanged historically.
The derived current age advances through the accepted event.
```

Authority is therefore temporal and scoped rather than a single blanket
numeric precedence:

```text
Genesis baseline at branch creation
-> accepted events and records in branch order
-> explicit supersessions or creator corrections
-> current derived state
```

A newer accepted record supersedes an older value only when it validly changes
the same fact. Unrelated records coexist. A Recorder summary cannot silently
override exact accepted prose or a higher-quality authoritative source.

### 3.3 Provisional canon

Provisional records are lower-confidence branch evidence. They may be explored
as true or false when needed, but neither interpretation becomes hard truth
silently.

When a scene depends on provisional canon:

- the dependent scene is also marked provisional;
- it carries every originating provisional canon ID;
- its consequences remain traceable to those IDs;
- later resolution can confirm or reject the lineage without rewriting history.

Provisional canon is not an invitation for CERA to resolve ambiguity on Ted's
behalf.

## 4. Runtime Codex retrieval

Python's initial packet is direction, not a complete dossier. It should contain
the immediate turn identity, user source, branch and authority boundaries,
current route, relevant hot context, and suggested retrieval starting points.

Runtime Codex receives bounded read/search/fetch tools over the complete
assigned world directory. It decides what evidence it needs and may expand its
lookup using character names, events, relationships, memories, threads, and
stable record IDs.

Recommended retrieval shape:

```text
small Python direction packet
-> Codex searches the isolated world index
-> Codex fetches exact authoritative records
-> Codex forms the causal and psychological sequence
-> Python validates identity and custody
```

Python remains the sole writer of authoritative records and transactional
state. Codex proposes interpretation and state changes; it does not directly
commit them.

### Benefits

- Older or indirect memories do not need to be repeated in every prompt.
- Codex can investigate an unexpected reference rather than guessing from a
  truncated dossier.
- Prompt size stays smaller while evidence remains available.
- New indexes can improve retrieval without changing story authority.

### Costs and risks

- The filesystem tool boundary must be real, not an instruction-only promise.
- Search results must distinguish references from exact evidence.
- Private knowledge still needs owner and audience metadata.
- Stale indexes must be regenerable and must never outrank canonical records.
- Every lookup should be traceable enough to diagnose a bad decision.

## 5. Character autonomy control

CERA exposes one chat-wide character-autonomy selector applied consistently to
all characters:

```text
off | mind | body | both
```

It sets the order of importance between the user's proposed outcome and the
character's own logic.

- `off`: the user's character-direction statements normally receive priority,
  subject to hard world contradictions.
- `mind`: the character's beliefs, judgment, personality, and decision process
  may reconsider or reject a user-declared choice.
- `body`: the user may establish a mental choice or dialogue, while physical
  feelings, reflexes, urges, hesitation, recoil, or movement can still diverge.
- `both`: independent character logic and bodily response both receive priority
  over a forced user outcome.

These are priorities, not absolute switches. Strong enough pressure, evidence,
or circumstance can overwhelm either mind or body. The Planner must adjudicate
the realistic sequence rather than use a rigid clause ledger.

A multi-action user message can contain completed actions, attempts, interrupted
actions, or desired outcomes. The Planner decides the reachable boundary from
timing, world state, autonomy, and reaction opportunity.

## 6. Planner and Writer ownership

### 6.1 Ordinary route

The ordinary route is:

```text
Python authority and direction
-> persistent Codex Planner with world-directory retrieval
-> DeepSeek Writer
-> provisional visible candidate
-> creator review
-> minimal accepted commit
-> Recorder-derived records
```

Codex owns causal sequence, psychology, likely choices, participation, and the
next meaningful stopping point. DeepSeek owns prose realization, chronology of
presentation, atmosphere, dialogue placement, staging, and sensory detail
within the adjudicated logic.

Do not respond to individual Writer mistakes by endlessly enlarging prompts,
schemas, checklists, or phrase rules. Change architecture only when varied
evidence demonstrates a generalized structural cause.

### 6.2 Adult route

Adult routing is automatic and visibly indicated in SillyTavern. The UI should
make the current reasoning owner clear, for example:

```text
ORDINARY - Codex logic
ADULT - DeepSeek logic
```

DeepSeek is not merely a decorative Writer in the adult route. It should make
its best scene-level causal and psychological decisions from the same relevant
world evidence, character state, and autonomy configuration. Its purpose is to
continue where the ordinary runtime component cannot operate, while remaining
coherent with the accepted branch.

Each user prompt message has exactly one logic owner. Python selects the owner
before generation. A message routed to DeepSeek remains DeepSeek-owned for that
entire candidate; it does not switch back to Codex partway through the same
visible reply. DeepSeek may emit a typed `return_to_codex` transition so the
next user message uses Codex logic.

CERA must not automatically copy arbitrary Codex or ChatGPT refusal heuristics
into its character logic. Facts such as age, consent, fear, law, risk, or social
expectations remain causal evidence. They can logically cause refusal,
retaliation, freezing, escape, concealment, compliance, conflict, or later
consequences; they are not blanket instructions to sanitize every event.

This design does not modify or evade any provider's own capabilities or
policies. Provider-specific limits remain external facts. CERA should not add
unrequested policy behavior merely to imitate a different provider.

At the route boundary:

- the outgoing owner records the sequence, character feelings, known facts,
  unresolved choices, and stopping boundary needed by the next owner;
- exact protected adult prose remains in its protected record;
- a synchronized, Codex-readable non-explicit causal projection is created;
- Python binds full and projected records by ID and hash;
- control returns to Codex only through an explicit typed transition.

The filtered causal projection is a required continuity artifact, not an
optional summary. Qualification must prove that it:

- preserves event order, participants, material outcomes, character reactions,
  knowledge changes, relationship effects, unresolved consequences, and the
  route-exit decision needed for later reasoning;
- excludes protected exact prose and details that Codex should not receive;
- remains bound to the protected full record by IDs and hashes;
- can be retrieved by Codex after the route returns to ordinary;
- survives process restart and branch fork without crossing world boundaries;
- gives Codex enough evidence to continue without inventing or silently losing
  what happened.

## 7. Review, recording, and recovery behavior

### 7.1 Accept and Recorder

The presentation-neutral accepted prose is the immutable visible story
artifact. Recorder output is derived evidence and cannot silently rewrite it.
When they conflict, preserve the accepted prose, mark recording incomplete or
conflicting, and repair the derived record.

Accept should commit the smallest safe accepted artifact first. Recorder work
then attaches relationship, knowledge, presence, material, thread, memory, and
development changes without regenerating the scene.

If exact accepted prose and its minimal branch receipt commit safely but Recorder
still cannot complete after the allowed repair, the story may continue. The
branch visibly remains `recording_pending`; derived fields that depend on the
missing record cannot become hard truth until recording succeeds. Later Recorder
repair attaches to the existing accepted artifact and never regenerates or
recommits the scene.

### 7.2 New chat and unresolved reviews

A newly created chat/world is independent. An unresolved review from another
chat must not block it or leak into it.

Within the current chat, the review/Recorder flow remains available so the
user's Accept or Decline can progress the story. If a clicked decision suffers
a transport failure, it should be safely replayable and idempotent rather than
creating a second candidate or double commit.

### 7.3 Restart

Restart restores the exact provisional candidate, its review identity, its
original buttons, and its branch lock without a new provider call. Candidate
IDs, review IDs, accepted ancestry, provider accounting, and persistent Planner
session lineage must survive restart.

### 7.4 Regenerate and Replan

`Regenerate` preserves the same accepted-state snapshot, original user source,
and causal constraints. The persistent Planner is asked whether another
realistic possibility exists. It does not invent an implausible opposite merely
to be different. If the character would still make essentially the same choice,
the regenerated result may remain similar.

`Replan` explicitly requests different causal reasoning. It is available from
the review/verification flow with an open text box; the text may be empty.
Creator guidance is stored as typed guidance rather than appended to the user's
story source.

### 7.5 Critical repair presentation

Automatic repair is limited to authoritative defects such as branch leakage,
identity conflicts, accepted-event contradictions, impossible presence,
protected-user violations, or equivalent hard-state failures. Ordinary prose
quality remains a creator-review matter.

When a critical candidate is removed or replaced, do not erase it before Ted
can inspect it. Keep it in a collapsed `View removed candidate` panel and in
immutable audit evidence. The replacement remains subject to the applicable
review decision.

## 8. Testing goals

### 8.1 Purpose of the twenty-turn campaign

The campaign simulates normal use long enough to expose common critical,
major, and general technical defects. It is not a demand for twenty perfect
first Writer samples.

The important failures include:

- API or transport errors;
- invalid or lost review IDs;
- inability to Accept, Decline, Regenerate, or Replan;
- branch/session contamination;
- duplicate commits or lost accepted state;
- wrong route or broken route transitions;
- restart or rehydration failure;
- Recorder corruption or unrecoverable pending state;
- repeated generalized logic/context defects.

A measured pass rate is more useful than an endless consecutive-clean target.
An isolated stochastic Writer miss is not automatically a backend bug.

### 8.2 Final creator-readiness smoke

Before CERA is handed back for creator testing, run six sequential story
prompts:

```text
3 ordinary prompts
3 adult prompts
```

The sequence must progress the same story, exercise ordinary/adult route
transitions, and finish without API, review, recording, persistence, or restart
errors. This is a technical pass. Ted remains the judge of subjective adult
prose quality.

The adult portion must additionally prove the complete projection loop:

```text
DeepSeek-owned accepted candidate
-> protected full record
-> filtered Codex-readable sequence
-> return_to_codex
-> later Codex retrieval and correct continuation
```

Repeat the projection retrieval after one process restart and one branch fork.
Verify that the fork inherited the source chat's filtered record and that an
unrelated new chat did not.

## 9. Current engineering strengths

The provider-free audit of commit
`58ae752cb7a4a24be4f43b6f1f67bf27f960ee0e` found meaningful strengths:

- accepted receipts use branch identity, parent/hash chaining, stale-head
  checks, and atomic publication;
- exact accepted prose commits before optional Recorder work;
- Writer views have manifests, hashes, custody checks, root confinement, and
  symlink rejection;
- the Pi route disables unrelated built-ins, skills, prompt files, retries, and
  compaction;
- HTTP is loopback-only with bearer checking, origin allowlisting, body limits,
  content-type validation, and no-store responses;
- no silent provider fallback or recursive retry is present;
- the exact audited source compiled and passed its provider-free test suite.

Audit verification for that snapshot:

```text
410 tracked Python files compiled
40/40 focused Pi-scene tests passed
1,277 full-suite tests passed
3 tests skipped because Windows symlink privileges were unavailable
0 failures or errors
```

Passing tests did not prove the runtime production-ready; targeted fault probes
found important uncovered defects.

## 10. Current engineering defects and risks

The following findings are snapshot-specific until corrected and reverified.

### Critical and high-priority

1. The Pi HTTP route binds one static session/world. Separate SillyTavern chats
   cannot reliably obtain independent branch context.
2. SillyTavern depth, autonomy, prompt-handling, effort, and regeneration fields
   are currently discarded by the Pi HTTP adapter.
3. Reviews, unresolved locks, candidate counters, and Planner thread identity
   are partly memory-only and can be lost or reused after restart.
4. Regenerate/Replan terminalizes the old review before the successor safely
   exists, so a failed successor can strand the branch.
5. Recorder record files and their completion head are not published as one
   atomic verified bundle; crash recovery can leave partial records.
6. Attached record hashes are not fully revalidated before later context reads.
7. There is no cumulative typed accepted-state reducer. Only a recent-six-turn
   window is read, while durable relationship, knowledge, presence, and memory
   changes can disappear or remain stuck at Genesis values.
8. Provider accounting and persistent Planner state can reset on process resume.
9. Adult route eligibility and the non-explicit projection boundary rely too
   heavily on prompts rather than typed, testable custody.
10. The Pi context-fetch protocol does not require exactly the expected bounded
    successful tool use, and truncation can be mislabeled as a normal stop.
11. HTTP maps materially different failures to generic non-retryable 400 errors,
    which produces opaque SillyTavern `Bad Request` failures.
12. The isolated-SillyTavern verifier trusts a prior manifest without fully
    rehashing the executable tree and checking copied user data.

### Maintainability and tooling

1. Durable DTO decoders coerce some wrong types instead of rejecting them. For
   example, the string `"false"` can become Boolean `true`.
2. Server ceiling command-line flags exist but are ignored in favor of hardcoded
   values.
3. The C75 worktree `.venv` points to a shared environment whose editable import
   resolves a different checkout unless `PYTHONPATH` is forced.
4. The repository lacks a reproducible formatter, linter, type-checker, coverage,
   and CI quality gate.
5. Several runtime, store, provider, and world modules are too large and combine
   unrelated responsibilities.
6. The nominally provider-neutral Pi runtime still depends on a concrete adapter
   instead of typed Writer and Recorder ports.
7. Current Pi schemas and source packages are not comprehensively covered by the
   global registry/inventory.
8. Tests are extensive but monolithic, and many assert prompt wording instead of
   restart, reducer, fault-ordering, branch-isolation, and configuration behavior.
9. The live service observed during the audit was running an older checkout than
   the audited C75 source. Source readiness and live-runtime identity must be
   reconciled before claiming a fix is active.

## 11. Mistakes and lessons learned

### 11.1 We overconstrained probabilistic model output

Repeated individual Writer misses led to more prompt rules, schemas, semantic
ledgers, validators, and phrase-level checks. This increased latency and
complexity without making stochastic prose deterministic.

Lesson: first classify whether a failure is transport, custody, missing context,
systematic model-interface design, or ordinary sample variation. Change source
only for a demonstrated generalized cause.

### 11.2 We sometimes optimized for the harness instead of the product

Some exact-replay and validation work allowed a new Planner output to change the
meaning being proved, then blamed the Writer for not satisfying the old target.

Lesson: freeze the actual proof authority. Do not add product restrictions to
repair a drifting test fixture.

### 11.3 We treated passing happy-path tests as broader readiness

The full provider-free suite passed, but fault probes exposed restart, atomicity,
branch isolation, record integrity, and route-boundary failures.

Lesson: qualification needs deliberate failure injection at transactional and
restart cut points, not just more ordinary successful cases.

### 11.4 We risked testing the wrong checkout

The shared virtual environment's editable install pointed at a different CERA
checkout. A normal command could report green results for the wrong source.

Lesson: every governed test entrypoint must assert its import root, commit, tree,
profile, and source hash before running.

### 11.5 We allowed volatile process state to masquerade as durable authority

Review IDs, counters, Planner sessions, and provider accounting were not all
restart-persistent.

Lesson: soft provider continuity may be in memory, but accepted state, pending
reviews, idempotency, accounting, and recovery identities must be durable Python
authority.

### 11.6 We used recent prose as a substitute for long-term state

A six-record context window cannot serve as the authoritative relationship,
knowledge, presence, and memory database.

Lesson: maintain a typed event-sourced branch reducer and checkpoint. Recent
prose is narrative context; it is not the world-state database.

### 11.7 We relied on prompt promises for deterministic boundaries

Adult projection privacy, route eligibility, and tool-call behavior were partly
instruction-only.

Lesson: Python must own identities, allowed roots, hashes, lifecycle state,
transaction boundaries, and exact protocol conformance. Models own narrative
meaning, not deterministic custody.

### 11.8 We exposed generic errors to the creator

Different backend failures became the same SillyTavern `Bad Request`, making
normal testing frustrating and diagnosis slow.

Lesson: return stable typed error codes, whether accepted state changed, the
review/candidate ID, a user-readable next action, and one debug-record location.

## 12. Engineering direction

Recommended correction order:

1. Per-chat branch/session isolation and real propagation of SillyTavern controls.
2. Durable reviews, candidate identities, Planner lineage, and cumulative
   provider accounting.
3. Atomic Recorder bundle publication, restart repair, and hash-verified reads.
4. Typed event-sourced branch-state reducer and checkpoints independent of the
   recent prose window.
5. Adult route eligibility, route indicator, protected full record, and bound
   Codex-readable projection.
6. Transaction-safe Regenerate/Replan behavior and collapsed removed-candidate
   presentation.
7. Strict DTO decoding, one immutable runtime profile, and worktree-safe tooling.
8. Incremental module decomposition and reproducible formatting, linting, typing,
   and CI gates.
9. Exact corrected-source smoke in an isolated SillyTavern environment.
10. Six-turn creator-readiness smoke followed by Ted's hands-on judgment.

Avoid a wholesale rewrite. Preserve the accepted-receipt, view-custody, local
HTTP security, and no-fallback strengths while replacing the unsafe seams.

## 13. Resolved creator decisions - 2026-08-09

1. A new chat uses the newest accepted Genesis revision. An existing chat remains
   pinned to its own world directory. A branch fork copies the selected source
   chat directory rather than starting again from new Genesis.
2. Each user prompt message has one logic owner. Ownership does not switch within
   one visible candidate. DeepSeek may request that the next message return to
   Codex.
3. A safely accepted turn may continue while Recorder is visibly pending. Exact
   accepted prose remains authority; incomplete derived state does not silently
   become hard truth.
4. The DeepSeek-to-Codex filtered sequence is a mandatory tested continuity
   artifact across ordinary return, restart, and branch-fork workflows.
5. A mixed prompt that crosses the adult-route boundary is routed as one
   DeepSeek-owned visible candidate. Codex may return a pre-generation typed
   handoff, but logic ownership does not split inside the response.
6. The global autonomy default is `both`. Mind and body are precedence controls,
   not absolute switches; sufficiently strong, evidence-supported pressure may
   overcome a tendency when the logic owner finds that realistic.
7. A normally valid candidate is accepted automatically after its required
   semantic and deterministic checks pass. One complete automatic repair is
   permitted for a critical authoritative defect. Continued failures remain
   visible and may be accepted only as explicitly marked provisional canon.
8. Regenerate reuses the exact accepted state, user source, controls, and
   evidence revision, then asks the logic owner for another realistic complete
   outcome. It may change the sequence only when another character-consistent
   decision is genuinely plausible. Replan means the reasoning itself is being
   challenged or deliberately changed.
9. A provisional assumption becomes pinned for its dependent lineage. Every
   dependent scene carries the originating provisional ID until explicit
   confirmation, correction, rejection, or supersession.
10. Concrete identity, birth, biological/legal family, and creator-declared
    lore are creator-locked by default. Evolvable state changes through accepted
    events. Hana is Mia's biological mother; Mia is Hana's biological daughter.
11. A false narrative assertion that contradicts locked authority is a critical
    correction. The literal user source remains preserved, but the false fact
    does not become canon. The same text spoken as dialogue remains an in-world
    claim that may be mistaken or deceptive.
12. Every relevant material character decision and material subconscious
    association is available to Ted in a collapsible SillyTavern view. CERA
    stores concise evidence-linked rationale, not hidden chain-of-thought.
13. Retained Codex sessions have a three-minute ordinary logic target. Tests do
    not lower quality settings to meet it; they record stage-level latency and
    explain material increases.
14. Voice, craft, and adult examples are Genesis-owned, versioned, read-only
    dependencies retrievable by concept or keyword. A world pins their manifest
    and hashes. They never establish story canon or character preference.
15. Runtime psychological pressures use qualitative evidence-linked categories,
    not provider-authored decimal coefficients. Repeated behavior may create a
    tendency or association, but stable-trait promotion requires explicit
    review.
16. The controlling implementation description for these decisions is
    `docs/implementation/CERA_FULL_MODEL_DECISION_RATIONALE_ROADMAP_V1.md`.
17. A separate ordinary Reader is restored for the active Pi route. After exact
   Writer prose freezes, Luna and Reader validate it concurrently and
   independently. Python automatically accepts by default only after both pass
   plus deterministic identity, custody, and privacy checks; a per-chat Manual
   Review mode may require explicit Accept. Rejection preserves the exact prose,
   reports the applicable concise Luna and Reader failures, and offers
   Regenerate, Decline, and auditable creator override. Adult protected prose is
   not sent to the Codex Reader; the existing protected Adult Filter receives
   the narrow severe-quality responsibility without another adult provider call.
   This durable provisional lifecycle must be provider-free qualified before
   live qualification. Controlling decision: D-219.

## 14. Update discipline

When a creator decision changes:

1. append the new decision and date here;
2. identify the earlier statement it supersedes;
3. record benefits, costs, and compatibility impact;
4. adopt it through the current manager and execution authority rather than
   rewriting historical queue files;
5. add tests at the owning abstraction;
6. update this document after implementation evidence shows what actually works.
